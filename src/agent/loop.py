"""Agent loop: call model → authorize → dispatch tools → append → trim → repeat."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from src.agent.context import trim_messages
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
) -> AgentResult:
    """Core harness loop. Extensible: swap registry / client / gate without changing this."""
    log = log or _default_log

    def emit(event: dict[str, Any]) -> None:
        if on_event is not None:
            on_event(event)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_task},
    ]
    tools = registry.openai_tools()

    log(f"[agent] model={client.config.model} max_steps={max_steps}")
    log(f"[agent] tools={', '.join(registry.names())}")
    if gate is not None:
        log(f"[agent] approval={gate.approval.value}")
    log(f"[agent] task={user_task!r}")
    emit(
        {
            "type": "run_start",
            "model": client.config.model,
            "max_steps": max_steps,
            "tools": registry.names(),
            "task": user_task,
        }
    )

    try:
        for step in range(1, max_steps + 1):
            log(f"\n=== step {step}/{max_steps} ===")
            emit({"type": "step_start", "step": step, "max_steps": max_steps})
            messages = trim_messages(messages, max_messages=max_messages)
            response = client.chat(messages, tools=tools)
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
                return AgentResult(
                    final_text=final,
                    steps=step,
                    stopped_reason="completed",
                    messages=messages,
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

                ok = not str(result).startswith("Error")
                result_summary = _summarize_result(result)
                log(f"[result] {result_summary}")
                emit(
                    {
                        "type": "tool_result",
                        "step": step,
                        "id": call_id,
                        "name": name,
                        "ok": ok,
                        "result": result if len(result) <= 4000 else result[:4000] + "\n...[truncated]",
                        "result_summary": result_summary,
                    }
                )
                if name == "todo_write":
                    todos = _parse_todo_lines(result)
                    if todos is not None:
                        emit({"type": "todo_update", "step": step, "todos": todos})

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": result,
                    }
                )

            emit({"type": "step_end", "step": step, "kind": "tools"})

        log(f"[agent] stopped: reached max_steps={max_steps}")
        final = f"(stopped after {max_steps} steps without a final answer)"
        emit({"type": "final", "step": max_steps, "text": final, "stopped_reason": "max_steps"})
        return AgentResult(
            final_text=final,
            steps=max_steps,
            stopped_reason="max_steps",
            messages=messages,
        )
    except KeyboardInterrupt:
        log("\n[agent] interrupted by user")
        emit({"type": "error", "message": "interrupted by user", "stopped_reason": "interrupted"})
        return AgentResult(
            final_text="(interrupted)",
            steps=0,
            stopped_reason="interrupted",
            messages=messages,
        )
    except Exception as exc:
        emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        raise
