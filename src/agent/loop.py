"""Agent loop: call model → authorize → dispatch tools → append → compress → repeat."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.agent.context import ContextManager, trim_messages
from src.agent.memory import append_run_to_memory, load_working_memory, save_working_memory
from src.agent.permissions import PermissionGate
from src.llm.client import LLMClient
from src.tools.base import ToolRegistry

LogFn = Callable[[str], None]
EventFn = Callable[[dict[str, Any]], None]


def _default_log(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class AgentResult:
    final_text: str
    steps: int
    stopped_reason: str  # completed | max_steps | interrupted
    messages: list[dict[str, Any]] = field(default_factory=list)
    memory: dict[str, Any] | None = None


def _message_to_dict(message: Any) -> dict[str, Any]:
    """Convert OpenAI SDK message object into a plain dict for history."""
    data: dict[str, Any] = {
        "role": message.role,
        "content": message.content,
    }
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        data["tool_calls"] = [
            {
                "id": tc.id,
                "type": getattr(tc, "type", "function") or "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in tool_calls
        ]
    return data


def _summarize_args(raw: str, limit: int = 120) -> str:
    raw = raw.replace("\n", "\\n")
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "..."


def _summarize_result(text: str, limit: int = 200) -> str:
    flat = text.replace("\n", "\\n")
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "..."


def _parse_args(raw: str | dict[str, Any]) -> dict[str, Any] | str:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        return f"Error: invalid JSON arguments: {exc}"
    if not isinstance(parsed, dict):
        return "Error: tool arguments must be a JSON object"
    return parsed


def _parse_todo_lines(result: str) -> list[dict[str, str]] | None:
    """Best-effort parse of todo_write render() output for the UI."""
    if not result.startswith("Todo list:"):
        return None
    items: list[dict[str, str]] = []
    for line in result.splitlines():
        m = re.match(r"\s*\[([ x>\-])\]\s*\(([^)]+)\)\s*(.+)$", line)
        if not m:
            continue
        mark, item_id, content = m.group(1), m.group(2), m.group(3).strip()
        status = {
            " ": "pending",
            "x": "completed",
            ">": "in_progress",
            "-": "cancelled",
        }.get(mark, "pending")
        items.append({"id": item_id, "content": content, "status": status})
    return items or None


_MUTATING_FILE_TOOLS = frozenset({"write_file", "edit_file"})
_FILE_DIFF_LIMIT = 120_000


def _snapshot_text(path: Any) -> str | None:
    """Read UTF-8 text for diff; None if missing / binary / unreadable."""
    try:
        if not path.exists() or not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _clip_diff(text: str | None) -> str | None:
    if text is None:
        return None
    if len(text) <= _FILE_DIFF_LIMIT:
        return text
    return text[:_FILE_DIFF_LIMIT] + f"\n...[truncated for UI, total {len(text)} chars]"


def run_agent(
    *,
    client: LLMClient,
    registry: ToolRegistry,
    system_prompt: str,
    user_task: str,
    max_steps: int,
    gate: PermissionGate | None = None,
    max_messages: int = 40,
    log: LogFn | None = None,
    on_event: EventFn | None = None,
    prior_messages: list[dict[str, Any]] | None = None,
    prior_memory: dict[str, Any] | None = None,
    context_token_budget: int | None = None,
    context_manager: ContextManager | None = None,
    persist_memory_md: bool = True,
    transcript_dir: Path | None = None,
) -> AgentResult:
    """Core harness loop with ACON-inspired Context Manager.

    prior_messages: slim prior session history for multi-turn continue
      (prefer memory + recent K + original task; not the full dump).
    prior_memory: ContextManager.export_memory() from the previous turn.
    ContextManager: observation compression + layered fold when over token budget.
    """
    log = log or _default_log

    def emit(event: dict[str, Any]) -> None:
        if on_event is not None:
            on_event(event)

    workdir = gate.workdir if gate is not None else getattr(client.config, "workdir", None)
    ctx = context_manager
    if ctx is None and workdir is not None:
        budget = context_token_budget
        if budget is None:
            budget = int(getattr(client.config, "context_token_budget", 8000) or 8000)
        ctx = ContextManager(
            workdir=workdir,
            tool_names=registry.names(),
            token_budget=budget,
            recent_keep_messages=max(8, min(max_messages // 2, 20)),
        )
        system_prompt = ctx.build_system_prompt()

    if ctx is not None and prior_memory:
        ctx.import_memory(prior_memory)
    elif ctx is not None and workdir is not None and not prior_messages:
        # New run: hydrate from dual-track working_memory.json if present
        disk_wm = load_working_memory(workdir)
        if disk_wm:
            ctx.import_memory(disk_wm)
            log("[agent] hydrated working memory from .agent/working_memory.json")

    tdir = transcript_dir
    if tdir is None:
        tdir = getattr(client.config, "transcript_dir", None)

    if prior_messages:
        messages = [m for m in prior_messages if m.get("role") != "system"]
        messages.insert(0, {"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_task})
    else:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_task},
        ]
    if ctx is not None:
        ctx.state.task = user_task
    tools = registry.openai_tools()

    log(f"[agent] model={client.config.model} max_steps={max_steps}")
    log(f"[agent] tools={', '.join(registry.names())}")
    if gate is not None:
        log(f"[agent] approval={gate.approval.value}")
    if ctx is not None:
        log(f"[agent] context_budget≈{ctx.token_budget} tokens (ACON-style manager)")
        if ctx.state.project_memory:
            log("[agent] loaded Project Memory from MEMORY.md")
        if prior_memory:
            log("[agent] hydrated working memory from prior session snapshot")
        log(f"[agent] prompt_layout={ctx.state.layout_mode}")
    cache_pol = getattr(client, "cache_policy", None)
    if cache_pol is not None:
        log(f"[agent] {cache_pol.describe()}")
    log(f"[agent] task={user_task!r}")
    emit(
        {
            "type": "run_start",
            "model": client.config.model,
            "max_steps": max_steps,
            "tools": registry.names(),
            "task": user_task,
            "context_token_budget": ctx.token_budget if ctx is not None else None,
            "has_project_memory": bool(ctx and ctx.state.project_memory),
            "prompt_layout": ctx.state.layout_mode if ctx is not None else None,
        }
    )

    def _finish(result: AgentResult) -> AgentResult:
        if ctx is not None and workdir is not None and result.memory is not None:
            wm_path = save_working_memory(
                workdir,
                result.memory,
                transcript_dir=tdir if isinstance(tdir, Path) else None,
            )
            if wm_path is not None:
                log(f"[memory] working_memory → {wm_path}")
                emit({"type": "working_memory_write", "path": str(wm_path)})
        if persist_memory_md and ctx is not None and workdir is not None:
            mem_path = append_run_to_memory(
                workdir,
                task=user_task,
                final_text=result.final_text,
                stopped_reason=result.stopped_reason,
                memory=result.memory,
            )
            if mem_path is not None:
                log(f"[memory] appended → {mem_path}")
                emit({"type": "memory_write", "path": str(mem_path)})
        return result

    try:
        for step in range(1, max_steps + 1):
            log(f"\n=== step {step}/{max_steps} ===")
            emit({"type": "step_start", "step": step, "max_steps": max_steps})
            if ctx is not None:
                model_messages = ctx.prepare_messages(messages, user_task=user_task)
            else:
                model_messages = trim_messages(messages, max_messages=max_messages)
            response = client.chat(model_messages, tools=tools)
            choice = response.choices[0]
            message = choice.message
            assistant_dict = _message_to_dict(message)
            messages.append(assistant_dict)

            tool_calls = assistant_dict.get("tool_calls") or []
            if not tool_calls:
                final = (assistant_dict.get("content") or "").strip()
                log(f"[agent] final:\n{final}")
                emit({"type": "final", "step": step, "text": final, "stopped_reason": "completed"})
                emit({"type": "step_end", "step": step, "kind": "final"})
                return _finish(
                    AgentResult(
                        final_text=final,
                        steps=step,
                        stopped_reason="completed",
                        messages=messages,
                        memory=ctx.export_memory() if ctx is not None else None,
                    )
                )

            if assistant_dict.get("content"):
                think = assistant_dict["content"]
                log(f"[think] {think}")
                emit({"type": "think", "step": step, "text": think})

            for tc in tool_calls:
                name = tc["function"]["name"]
                raw_args = tc["function"]["arguments"]
                call_id = tc["id"]
                args_summary = _summarize_args(raw_args)
                log(f"[tool] {name}({args_summary})")
                emit(
                    {
                        "type": "tool_call",
                        "step": step,
                        "id": call_id,
                        "name": name,
                        "arguments": raw_args if isinstance(raw_args, str) else json.dumps(raw_args),
                        "arguments_summary": args_summary,
                    }
                )

                parsed = _parse_args(raw_args)
                before_text: str | None = None
                rel_path: str | None = None
                resolved_path = None
                if (
                    name in _MUTATING_FILE_TOOLS
                    and gate is not None
                    and isinstance(parsed, dict)
                    and parsed.get("path")
                ):
                    try:
                        resolved_path = gate.resolve_path(str(parsed["path"]))
                        rel_path = resolved_path.relative_to(gate.workdir).as_posix()
                        before_text = _snapshot_text(resolved_path)
                    except (OSError, ValueError):
                        resolved_path = None
                        rel_path = str(parsed.get("path") or "")

                if isinstance(parsed, str):
                    result = parsed
                elif gate is not None:
                    decision = gate.authorize(name, parsed)
                    if not decision.allowed:
                        result = f"Error: tool denied by permission gate: {decision.reason}"
                        log(f"[deny] {result}")
                    else:
                        result = registry.dispatch(name, parsed)
                else:
                    result = registry.dispatch(name, parsed)

                stored = result
                if ctx is not None:
                    stored = ctx.observe_tool(
                        step=step,
                        tool_name=name,
                        raw_args=parsed if isinstance(parsed, dict) else raw_args,
                        result=result,
                    )

                ok = not str(result).startswith("Error")
                result_summary = _summarize_result(stored)
                log(f"[result] {result_summary}")
                emit(
                    {
                        "type": "tool_result",
                        "step": step,
                        "id": call_id,
                        "name": name,
                        "ok": ok,
                        "result": stored if len(stored) <= 4000 else stored[:4000] + "\n...[truncated]",
                        "result_summary": result_summary,
                        "compressed": stored != result,
                    }
                )
                if name == "todo_write":
                    todos = _parse_todo_lines(result)
                    if todos is not None:
                        emit({"type": "todo_update", "step": step, "todos": todos})
                if ok and name in _MUTATING_FILE_TOOLS and rel_path is not None:
                    after_text = _snapshot_text(resolved_path) if resolved_path is not None else None
                    if after_text is None and isinstance(parsed, dict) and "content" in parsed:
                        after_text = str(parsed.get("content") or "")
                    emit(
                        {
                            "type": "file_change",
                            "step": step,
                            "id": call_id,
                            "tool": name,
                            "path": rel_path,
                            "old_content": _clip_diff(before_text if before_text is not None else ""),
                            "new_content": _clip_diff(after_text if after_text is not None else ""),
                            "is_new": before_text is None,
                        }
                    )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": stored,
                    }
                )

            emit({"type": "step_end", "step": step, "kind": "tools"})

        log(f"[agent] stopped: reached max_steps={max_steps}")
        final = f"(stopped after {max_steps} steps without a final answer)"
        emit({"type": "final", "step": max_steps, "text": final, "stopped_reason": "max_steps"})
        return _finish(
            AgentResult(
                final_text=final,
                steps=max_steps,
                stopped_reason="max_steps",
                messages=messages,
                memory=ctx.export_memory() if ctx is not None else None,
            )
        )
    except KeyboardInterrupt:
        log("\n[agent] interrupted by user")
        emit({"type": "error", "message": "interrupted by user", "stopped_reason": "interrupted"})
        return _finish(
            AgentResult(
                final_text="(interrupted)",
                steps=0,
                stopped_reason="interrupted",
                messages=messages,
                memory=ctx.export_memory() if ctx is not None else None,
            )
        )
    except Exception as exc:
        emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        raise
