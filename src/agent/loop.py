"""Agent loop: call model → authorize → dispatch tools → append → compress → repeat."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.agent.completion_gate import (
    build_evidence_nudge_message,
    fake_green_warn_payload,
    is_fake_green,
    note_completion_nudge,
    should_block_completion,
)
from src.agent.cancel import build_interrupt_message, is_cancelled
from src.agent.context import ContextManager, sanitize_tool_pairing, trim_messages
from src.agent.loop_guard import LoopGuard, tool_call_fingerprint
from src.agent.memory import (
    append_run_to_memory,
    format_turn_summary,
    load_working_memory,
    save_turn_summary_file,
    save_working_memory,
)
from src.agent.permissions import PermissionGate
from src.agent.retry_policy import (
    RetryPolicy,
    call_with_transient_retry,
    classify_failure,
    format_failure_suffix,
    transient_exhausted_suffix,
)
from src.agent.skills import format_skill_preload, suggest_skills
from src.agent.steer import SteerInbox, format_steer_message
from src.agent.stop_conditions import (
    build_final_nudge_message,
    clear_nudge_state,
    evaluate_final_nudge,
    force_stop_message,
    is_mutating_tool,
    post_nudge_mutating_suffix,
    reasons_allow_force_stop,
    should_force_stop_after_nudge,
)
from src.agent.task_budget import TaskBudget
from src.agent.tool_visibility import infer_phase, visible_tool_names
from src.llm.client import LLMClient
from src.tools.base import ToolRegistry

# Re-export for smoke tests / callers that imported from loop
__all__ = [
    "AgentResult",
    "LoopGuard",
    "TaskBudget",
    "run_agent",
    "tool_call_fingerprint",
]

LogFn = Callable[[str], None]
EventFn = Callable[[dict[str, Any]], None]


def _default_log(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class AgentResult:
    final_text: str
    steps: int
    stopped_reason: str  # completed | max_steps | interrupted | loop_detected | cycle_detected | stagnation_detected | retry_exhausted | goal_met_forced | budget_exhausted
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
    cancel_event: Any | None = None,
    steer_inbox: SteerInbox | None = None,
    max_task_tokens: int | None = None,
) -> AgentResult:
    """Core harness loop with ACON-inspired Context Manager.

    prior_messages: slim prior session history for multi-turn continue
      (prefer memory + recent K + original task; not the full dump).
    prior_memory: ContextManager.export_memory() from the previous turn.
    ContextManager: observation compression + layered fold when over token budget.
    cancel_event: optional threading.Event; when set, stop cooperatively
      (stopped_reason=interrupted) after the current LLM/tool boundary.
    steer_inbox: optional mid-run user corrections drained between steps.
    """
    log = log or _default_log

    def emit(event: dict[str, Any]) -> None:
        if on_event is not None:
            on_event(event)

    mutated_paths: list[str] = []

    def _interrupt_result(*, step: int, messages: list[dict[str, Any]]) -> AgentResult:
        final = build_interrupt_message(changed_files=mutated_paths)
        log("\n[agent] interrupted by user")
        emit(
            {
                "type": "final",
                "step": step,
                "text": final,
                "stopped_reason": "interrupted",
                "changed_files": list(dict.fromkeys(mutated_paths)),
            }
        )
        return _finish(
            AgentResult(
                final_text=final,
                steps=step,
                stopped_reason="interrupted",
                messages=messages,
                memory=ctx.export_memory() if ctx is not None else None,
            )
        )

    def _drain_steers(messages: list[dict[str, Any]], *, step: int) -> bool:
        """Inject pending mid-run user steers. Returns True if any injected."""
        if steer_inbox is None:
            return False
        items = steer_inbox.drain()
        if not items:
            return False
        for text in items:
            block = format_steer_message(text)
            messages.append({"role": "user", "content": block})
            log(f"[agent] steer@{step}: {text[:160]!r}")
            emit({"type": "steer", "step": step, "text": text})
        if ctx is not None:
            clear_nudge_state(ctx.task_state)
            ctx.post_nudge_mutating = 0
            # Latest steer becomes the active goal signal
            latest = items[-1].strip()[:500]
            ctx.state.task = latest
            ctx.task_state.goal = latest
        return True

    workdir = gate.workdir if gate is not None else getattr(client.config, "workdir", None)
    ctx = context_manager
    if ctx is None and workdir is not None:
        budget = context_token_budget
        if budget is None:
            budget = int(getattr(client.config, "context_token_budget", 32000) or 32000)
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

    # Keyword skill router: high-confidence match → preload body (saves load_skill step)
    preloaded_skill: str | None = None
    preloaded_meta: dict[str, Any] | None = None
    suggestions = suggest_skills(user_task)
    if suggestions:
        pick = suggestions[0]
        block = format_skill_preload(pick.name, score=pick.score, matched=pick.matched)
        if block:
            last = messages[-1]
            if last.get("role") == "user":
                last["content"] = f"{last.get('content') or ''}\n\n{block}"
            else:
                messages.append({"role": "user", "content": block})
            preloaded_skill = pick.name
            preloaded_meta = {
                "name": pick.name,
                "score": pick.score,
                "matched": list(pick.matched),
                "via": "keyword_router",
            }
            log(
                f"[agent] skill_preload={pick.name} score={pick.score} "
                f"hits={list(pick.matched)[:4]}"
            )

    messages = sanitize_tool_pairing(messages)
    if ctx is not None:
        # Latest user message defines the active goal (do not keep stale WM goal)
        ctx.ensure_task_goal(user_task, replace=True)
        # Each new user message may ask for more edits — never inherit a stale
        # "already nudged / force-stop / tests green" latch from a previous turn.
        clear_nudge_state(ctx.task_state)
        ctx.post_nudge_mutating = 0
    tools = registry.openai_tools()  # may be narrowed each step when visibility=auto
    cfg = client.config

    log(f"[agent] model={cfg.model} max_steps={max_steps}")
    log(f"[agent] tools={', '.join(registry.names())}")
    if gate is not None:
        log(
            f"[agent] approval={gate.approval.value}"
            + (f" deny_high={gate.deny_high}" if getattr(gate, "deny_high", False) else "")
        )
    tool_visibility = str(getattr(cfg, "tool_visibility", "auto") or "auto")
    completion_mode = str(getattr(cfg, "completion_mode", "evidence") or "evidence")
    evidence_nudge_max = int(getattr(cfg, "evidence_nudge_max", 2) or 2)
    fake_green_mode = str(getattr(cfg, "fake_green_mode", "block") or "block")
    task_tok_cap = (
        int(max_task_tokens)
        if max_task_tokens is not None
        else int(getattr(cfg, "max_task_tokens", 0) or 0)
    )
    out_reserve = int(getattr(cfg, "task_token_output_reserve", 512) or 512)
    task_budget = TaskBudget(max_task_tokens=task_tok_cap, output_reserve=out_reserve)
    log(
        f"[agent] tool_visibility={tool_visibility} "
        f"completion_mode={completion_mode} evidence_nudge_max={evidence_nudge_max} "
        f"fake_green_mode={fake_green_mode}"
    )
    if task_budget.enabled:
        log(
            f"[agent] max_task_tokens={task_budget.max_task_tokens} "
            f"(output_reserve={task_budget.output_reserve}; Cost-A hard gate ON)"
        )
    else:
        log("[agent] max_task_tokens=0 (Cost-A hard gate OFF)")
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
            "max_task_tokens": task_budget.max_task_tokens if task_budget.enabled else 0,
            "has_project_memory": bool(ctx and ctx.state.project_memory),
            "prompt_layout": ctx.state.layout_mode if ctx is not None else None,
            "preloaded_skill": preloaded_skill,
        }
    )
    if preloaded_meta is not None:
        emit({"type": "skill_loaded", **preloaded_meta})

    loop_guard = LoopGuard.from_env(
        warn_after=getattr(cfg, "loop_warn_after", None),
        stop_after=getattr(cfg, "loop_stop_after", None),
        error_nudge_after=getattr(cfg, "loop_error_nudge_after", None),
    )
    retry_max = int(getattr(cfg, "retry_max_failures", 3) or 3)
    nudge_mutating_limit = int(getattr(cfg, "final_nudge_mutating_limit", 2) or 2)
    if ctx is not None:
        # Prefer config over whatever was hydrated; keep failed history
        ctx.retry_policy.max_failures = retry_max
    else:
        # No context manager — still track retries locally
        pass
    retry_policy = ctx.retry_policy if ctx is not None else RetryPolicy(max_failures=retry_max)
    log(
        f"[agent] loop_guard warn={loop_guard.warn_after} "
        f"stop={loop_guard.stop_after} error_nudge={loop_guard.error_nudge_after} "
        f"cycle_warn={loop_guard.cycle_warn_repeats} "
        f"cycle_stop={loop_guard.cycle_stop_repeats} "
        f"stag_warn={loop_guard.stagnation_warn_after} "
        f"stag_stop={loop_guard.stagnation_stop_after} "
        f"retry_max={retry_max} final_nudge_mutating_limit={nudge_mutating_limit}"
    )

    def _finish(result: AgentResult) -> AgentResult:
        usage = None
        if ctx is not None:
            usage = ctx.usage_report(result.messages, scope="turn")
            emit({"type": "context_usage", **usage})
            if result.memory is None:
                result.memory = ctx.export_memory()
            if isinstance(result.memory, dict) and usage is not None:
                result.memory["context_usage"] = usage
            if usage is not None:
                task_budget.note_context_usage(usage.get("used_tokens"))
        if result.memory is None:
            result.memory = {}
        cost = task_budget.cost_report(
            steps=result.steps,
            max_steps=max_steps,
            stopped_reason=result.stopped_reason,
        )
        if isinstance(result.memory, dict):
            result.memory["task_budget"] = task_budget.snapshot()
            result.memory["cost_report"] = cost
        emit({"type": "cost_report", **cost})
        if ctx is not None and workdir is not None and result.memory is not None:
            wm_path = save_working_memory(
                workdir,
                result.memory,
                transcript_dir=tdir if isinstance(tdir, Path) else None,
            )
            if wm_path is not None:
                log(f"[memory] working_memory → {wm_path}")
                emit({"type": "working_memory_write", "path": str(wm_path)})
                ts = result.memory.get("task_state") if isinstance(result.memory, dict) else None
                if ts:
                    emit({"type": "task_state", "task_state": ts})
        if persist_memory_md and ctx is not None and workdir is not None:
            summary = format_turn_summary(
                task=user_task,
                final_text=result.final_text,
                stopped_reason=result.stopped_reason,
                memory=result.memory,
                usage=usage,
            )
            if result.memory is not None:
                result.memory["turn_summary"] = summary
            mem_path = append_run_to_memory(
                workdir,
                task=user_task,
                final_text=result.final_text,
                stopped_reason=result.stopped_reason,
                memory=result.memory,
                usage=usage,
            )
            if mem_path is not None:
                log(f"[memory] appended → {mem_path}")
                emit({"type": "memory_write", "path": str(mem_path)})
            summary_path = save_turn_summary_file(workdir, summary)
            emit(
                {
                    "type": "turn_summary",
                    "text": summary,
                    "path": str(summary_path) if summary_path else None,
                    "context_usage": usage,
                }
            )
            log("[memory] turn summary written")
        return result

    def _budget_stop(step: int, decision: dict[str, Any], messages: list[dict[str, Any]]) -> AgentResult:
        final = task_budget.stop_message(decision)
        log(
            f"[agent] stopped: budget_exhausted kind={decision.get('budget_kind')} "
            f"used≈{decision.get('tokens_used')}/{decision.get('max_task_tokens')} "
            f"projected≈{decision.get('projected')} (no further LLM call)"
        )
        emit(
            {
                "type": "budget_exhausted",
                "step": step,
                **{k: v for k, v in decision.items() if k != "allow"},
            }
        )
        emit(
            {
                "type": "final",
                "step": step,
                "text": final,
                "stopped_reason": "budget_exhausted",
                "budget_kind": decision.get("budget_kind"),
            }
        )
        emit({"type": "step_end", "step": step, "kind": "budget_exhausted"})
        return _finish(
            AgentResult(
                final_text=final,
                steps=step,
                stopped_reason="budget_exhausted",
                messages=messages,
                memory=ctx.export_memory() if ctx is not None else None,
            )
        )

    try:
        for step in range(1, max_steps + 1):
            if is_cancelled(cancel_event):
                return _interrupt_result(step=max(0, step - 1), messages=messages)

            _drain_steers(messages, step=step)

            log(f"\n=== step {step}/{max_steps} ===")
            emit({"type": "step_start", "step": step, "max_steps": max_steps})

            # Cost-B: budget awareness in Current State + one-shot ≤20% warn
            if ctx is not None:
                ctx.state.budget_line = task_budget.format_line(step=step, max_steps=max_steps)
            warn_msg = task_budget.maybe_warn_message(
                step=max(0, step - 1),
                max_steps=max_steps,
            )
            if warn_msg:
                messages.append({"role": "user", "content": warn_msg})
                log(f"[budget] {warn_msg[:120]}")
                emit({"type": "budget_warn", "step": step, "text": warn_msg})

            # Least-privilege: narrow visible tools from todo phase (optional)
            if tool_visibility == "auto":
                todos_text = ctx.state.todos_text if ctx is not None else ""
                goal = ctx.task_state.goal if ctx is not None else user_task
                files_mutated = bool(ctx and ctx.task_state.files_mutated)
                tests_passed = (
                    ctx.task_state.test_status.passed
                    if ctx and ctx.task_state.test_status
                    else None
                )
                phase = infer_phase(
                    todos_text=todos_text,
                    goal=goal,
                    files_mutated=files_mutated,
                    tests_passed=tests_passed,
                )
                vis_names = visible_tool_names(registry, phase)
                tools = registry.openai_tools(names=vis_names)
                if ctx is not None:
                    ctx.task_state.tool_phase = phase
                log(f"[tool_visibility] phase={phase} tools={', '.join(vis_names)}")
                emit(
                    {
                        "type": "tool_visibility",
                        "step": step,
                        "phase": phase,
                        "tools": vis_names,
                    }
                )
            else:
                tools = registry.openai_tools()

            if ctx is not None:
                model_messages = ctx.prepare_messages(messages, user_task=user_task)
                usage_evt = ctx.usage_report(model_messages, scope="turn")
                emit({"type": "context_usage", **usage_evt})
                task_budget.note_context_usage(
                    usage_evt.get("used_tokens"),
                    compress_events=int(getattr(ctx.state, "compress_events", 0) or 0)
                    + int(getattr(ctx.state, "fold_events", 0) or 0)
                    + int(getattr(ctx.state, "microcompact_events", 0) or 0),
                )
            else:
                model_messages = trim_messages(messages, max_messages=max_messages)

            # Cost-A: sync token gate BEFORE the provider call
            deny = task_budget.check_before_llm(model_messages)
            if deny is not None:
                # Stop before this LLM call; steps = completed LLM rounds so far
                return _budget_stop(
                    step=max(0, task_budget.llm_calls),
                    decision=deny,
                    messages=messages,
                )

            response = client.chat(model_messages, tools=tools)
            if is_cancelled(cancel_event):
                return _interrupt_result(step=step, messages=messages)
            choice = response.choices[0]
            message = choice.message
            assistant_dict = _message_to_dict(message)
            messages.append(assistant_dict)
            task_budget.record_llm_turn(
                messages=model_messages,
                response=response,
                assistant_message=assistant_dict,
            )
            emit(
                {
                    "type": "task_budget",
                    "step": step,
                    "max_steps": max_steps,
                    "line": task_budget.format_line(step=step, max_steps=max_steps),
                    **task_budget.snapshot(),
                }
            )

            tool_calls = assistant_dict.get("tool_calls") or []
            if not tool_calls:
                # Mid-run steer arrived while model tried to finish → keep going
                if _drain_steers(messages, step=step):
                    emit({"type": "step_end", "step": step, "kind": "steered"})
                    continue
                final = (assistant_dict.get("content") or "").strip()
                # Completion Evidence Gate: refuse "done" without Mustlist evidence
                if ctx is not None:
                    block, why = should_block_completion(
                        ctx.task_state,
                        completion_mode=completion_mode,
                        max_nudges=evidence_nudge_max,
                        fake_green_mode=fake_green_mode,
                    )
                    if block:
                        n = note_completion_nudge(ctx.task_state)
                        nudge = build_evidence_nudge_message(ctx.task_state, reason=why)
                        messages.append({"role": "user", "content": nudge})
                        log(f"[completion_gate] blocked ({why}); nudge {n}/{evidence_nudge_max}")
                        emit(
                            {
                                "type": "completion_gate",
                                "step": step,
                                "blocked": True,
                                "reason": why,
                                "nudge": n,
                                "max_nudges": evidence_nudge_max,
                                "text": nudge,
                                "mutated_paths": list(ctx.task_state.mutated_paths)[:12],
                            }
                        )
                        emit({"type": "step_end", "step": step, "kind": "completion_gate"})
                        continue
                    if why.startswith("evidence nudge budget"):
                        log(f"[completion_gate] allowing complete without evidence ({why})")
                        emit(
                            {
                                "type": "completion_gate",
                                "step": step,
                                "blocked": False,
                                "reason": why,
                            }
                        )
                    elif why == "tests passed with fake_green_warn" or (
                        is_fake_green(ctx.task_state)
                        and fake_green_mode.strip().lower() == "warn"
                    ):
                        warn_ev = fake_green_warn_payload(ctx.task_state)
                        warn_ev["step"] = step
                        log(f"[completion_gate] {warn_ev.get('text')}")
                        emit(warn_ev)

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

            loop_guard.begin_step()
            step_had_failure = False

            for tc in tool_calls:
                if is_cancelled(cancel_event):
                    return _interrupt_result(step=step, messages=messages)

                name = tc["function"]["name"]
                raw_args = tc["function"]["arguments"]
                call_id = tc["id"]
                args_summary = _summarize_args(raw_args)
                task_budget.record_tool(name)
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
                fp_args: dict[str, Any] | str = parsed if isinstance(parsed, dict) else raw_args
                streak, fp = loop_guard.observe(name, fp_args)
                cycle_hit = loop_guard.cycle_status()
                if streak >= loop_guard.warn_after:
                    log(f"[loop_guard] streak={streak} tool={name}")
                    emit(
                        {
                            "type": "loop_warning",
                            "step": step,
                            "name": name,
                            "streak": streak,
                            "fingerprint": fp[:200],
                            "will_stop": streak >= loop_guard.stop_after,
                        }
                    )
                if cycle_hit is not None:
                    log(
                        f"[loop_guard] cycle level={cycle_hit.level} "
                        f"period={cycle_hit.period} repeats={cycle_hit.repeats}"
                    )
                    emit(
                        {
                            "type": "cycle_warning",
                            "step": step,
                            "name": name,
                            "level": cycle_hit.level,
                            "period": cycle_hit.period,
                            "repeats": cycle_hit.repeats,
                            "will_stop": cycle_hit.level == "stop",
                        }
                    )

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

                reused = False
                hard_blocked = False
                cached = loop_guard.same_step_lookup(fp)
                if retry_policy.is_blocked(fp):
                    # Dec-A D3: exhausted strategy → hard BLOCK at dispatch (no handler)
                    result = retry_policy.blocked_tool_message(name)
                    hard_blocked = True
                    log(f"[retry_policy] BLOCKED fingerprint tool={name}")
                    emit(
                        {
                            "type": "strategy_blocked",
                            "step": step,
                            "id": call_id,
                            "name": name,
                            "fingerprint": fp[:200],
                            "block_hits": retry_policy.block_hits,
                        }
                    )
                elif cached is not None:
                    result = LoopGuard.dedup_reuse_message(name, cached)
                    reused = True
                    log(f"[dedup] reusing same-step result for {name}")
                    emit(
                        {
                            "type": "action_dedup",
                            "step": step,
                            "id": call_id,
                            "name": name,
                            "fingerprint": fp[:200],
                        }
                    )
                elif isinstance(parsed, str):
                    result = parsed
                elif gate is not None:
                    decision = gate.authorize(name, parsed, call_id=call_id)
                    if decision.allowed and "user approved" in decision.reason:
                        decision_label = "confirm"
                    elif decision.allowed:
                        decision_label = "allow"
                    else:
                        decision_label = "deny"
                    emit(
                        {
                            "type": "auth_decision",
                            "step": step,
                            "id": call_id,
                            "name": name,
                            "allowed": decision.allowed,
                            "reason": decision.reason,
                            "risk_level": decision.risk_level,
                            "decision": decision_label,
                        }
                    )
                    if not decision.allowed:
                        result = f"错误：权限门拒绝了该工具 — {decision.reason}"
                        log(f"[deny] {result}")
                    else:
                        result, tr = call_with_transient_retry(
                            lambda: registry.dispatch(name, parsed),
                            sleep_fn=lambda _s: None,  # keep tool path snappy in-loop
                        )
                        if tr.attempts > 1:
                            log(
                                f"[retry_policy] transient tool attempts={tr.attempts} "
                                f"recovered={tr.recovered} tool={name}"
                            )
                            emit(
                                {
                                    "type": "transient_retry",
                                    "step": step,
                                    "id": call_id,
                                    "name": name,
                                    "attempts": tr.attempts,
                                    "recovered": tr.recovered,
                                    "kind": tr.kind,
                                }
                            )
                else:
                    result, tr = call_with_transient_retry(
                        lambda: registry.dispatch(name, parsed),
                        sleep_fn=lambda _s: None,
                    )
                    if tr.attempts > 1:
                        log(
                            f"[retry_policy] transient tool attempts={tr.attempts} "
                            f"recovered={tr.recovered} tool={name}"
                        )
                        emit(
                            {
                                "type": "transient_retry",
                                "step": step,
                                "id": call_id,
                                "name": name,
                                "attempts": tr.attempts,
                                "recovered": tr.recovered,
                                "kind": tr.kind,
                            }
                        )

                stored = result
                if ctx is not None and not reused:
                    stored = ctx.observe_tool(
                        step=step,
                        tool_name=name,
                        raw_args=parsed if isinstance(parsed, dict) else raw_args,
                        result=result,
                    )
                    if isinstance(stored, str) and stored.startswith("[soft-dedup]"):
                        emit(
                            {
                                "type": "soft_dedup",
                                "step": step,
                                "id": call_id,
                                "name": name,
                                "path": (
                                    str(parsed.get("path") or "")
                                    if isinstance(parsed, dict)
                                    else ""
                                ),
                                "text": stored[:400],
                                "soft_dedup_events": int(
                                    getattr(ctx.state, "soft_dedup_events", 0) or 0
                                ),
                            }
                        )
                elif ctx is not None and reused:
                    ctx.task_state.update_from_tool(
                        tool_name=name,
                        args=parsed if isinstance(parsed, dict) else None,
                        result=cached or result,
                        paths=None,
                    )

                if not reused:
                    loop_guard.same_step_store(fp, stored)

                # Timeline: model-initiated load_skill (when not already preloaded)
                if (
                    name == "load_skill"
                    and isinstance(parsed, dict)
                    and not str(result).startswith("Error")
                    and not hard_blocked
                ):
                    loaded_name = str(parsed.get("name") or "").strip()
                    if loaded_name and loaded_name != preloaded_skill:
                        emit(
                            {
                                "type": "skill_loaded",
                                "name": loaded_name,
                                "via": "load_skill",
                                "step": step,
                            }
                        )

                # Outcome for error-streak: use underlying tool body, not dedup wrapper
                body_for_ok = cached if reused else result
                ok = not str(body_for_ok).startswith("Error")
                # Test failures via exit_code also count as not-ok for streak
                if ok and isinstance(body_for_ok, str):
                    if re.search(r"(?m)^(FAILED|ERROR)\b|exit_code:\s*[1-9]", body_for_ok):
                        ok = False

                err_streak = loop_guard.record_outcome(fp, ok=ok)
                if err_streak >= loop_guard.error_nudge_after and not hard_blocked:
                    log(f"[loop_guard] error_streak={err_streak} tool={name}")
                    emit(
                        {
                            "type": "error_streak",
                            "step": step,
                            "name": name,
                            "error_streak": err_streak,
                            "fingerprint": fp[:200],
                        }
                    )

                obs_streak = 0
                if not reused and not hard_blocked:
                    obs_streak = loop_guard.record_observation(name, str(body_for_ok))
                    if obs_streak >= loop_guard.stagnation_warn_after:
                        log(f"[loop_guard] obs_streak={obs_streak} tool={name}")
                        emit(
                            {
                                "type": "stagnation_warning",
                                "step": step,
                                "name": name,
                                "obs_streak": obs_streak,
                                "will_stop": (
                                    loop_guard.stagnation_stop_after > 0
                                    and obs_streak >= loop_guard.stagnation_stop_after
                                ),
                            }
                        )

                if hard_blocked:
                    # Already banned; do not re-record. Stop after tool message is written.
                    step_had_failure = True
                elif not ok:
                    step_had_failure = True
                    fail_kind = classify_failure(result=str(body_for_ok)) or "semantic"
                    if fail_kind == "format":
                        stored = f"{stored}{format_failure_suffix(tool_name=name)}"
                        log(f"[retry_policy] format failure tool={name} (no strategy ban)")
                        emit(
                            {
                                "type": "retry_stage",
                                "step": step,
                                "name": name,
                                "failure_kind": "format",
                                "banned": False,
                                "will_stop": False,
                            }
                        )
                    elif fail_kind == "transient":
                        # Auto-retry already exhausted inside dispatch wrapper
                        stored = f"{stored}{transient_exhausted_suffix(tool_name=name, attempts=1)}"
                        log(f"[retry_policy] transient exhausted tool={name} (no strategy ban)")
                        emit(
                            {
                                "type": "retry_stage",
                                "step": step,
                                "name": name,
                                "failure_kind": "transient",
                                "banned": False,
                                "will_stop": False,
                            }
                        )
                    else:
                        retry_decision = retry_policy.record_failure(
                            tool_name=name,
                            args=parsed if isinstance(parsed, dict) else None,
                            result=str(body_for_ok),
                            kind="semantic",
                        )
                        if retry_decision is not None:
                            if retry_decision.should_stop:
                                retry_policy.ban_fingerprint(fp)
                            if ctx is not None:
                                ctx._sync_task_retry_fields()
                            log(
                                f"[retry_policy] kind=semantic key={retry_decision.key} "
                                f"stage={retry_decision.stage}/{retry_policy.max_failures} "
                                f"count={retry_decision.count}"
                                f"{' BAN' if retry_decision.should_stop else ''}"
                            )
                            emit(
                                {
                                    "type": "retry_stage",
                                    "step": step,
                                    "name": name,
                                    "failure_kind": "semantic",
                                    "failure_key": retry_decision.key,
                                    "stage": retry_decision.stage,
                                    "count": retry_decision.count,
                                    "max_failures": retry_policy.max_failures,
                                    "will_stop": False,
                                    "banned": retry_decision.should_stop,
                                    "failed_strategies": [
                                        s.to_dict()
                                        for s in list(retry_policy.by_key.values())[:8]
                                    ],
                                }
                            )
                            if retry_decision.suffix:
                                stored = f"{stored}{retry_decision.suffix}"
                        # Dec-A/E3: exhausted → ban fingerprint; hard stop waits for
                        # next same-fp dispatch (BLOCK). No strategy auto-replay.

                # After tests-passed nudge only: mutating tools may escalate to force-stop
                if (
                    not hard_blocked
                    and ctx is not None
                    and ctx.task_state.final_nudge_sent
                    and reasons_allow_force_stop(ctx.task_state.stop_nudge_reasons)
                    and is_mutating_tool(name)
                    and not reused
                ):
                    ctx.post_nudge_mutating += 1
                    stored = (
                        f"{stored}"
                        f"{post_nudge_mutating_suffix(count=ctx.post_nudge_mutating, limit=nudge_mutating_limit)}"
                    )
                    emit(
                        {
                            "type": "final_nudge_warning",
                            "step": step,
                            "name": name,
                            "post_nudge_mutating": ctx.post_nudge_mutating,
                            "limit": nudge_mutating_limit,
                        }
                    )
                    if should_force_stop_after_nudge(
                        mutating_count=ctx.post_nudge_mutating,
                        limit=nudge_mutating_limit,
                        reasons=ctx.task_state.stop_nudge_reasons,
                    ):
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
                                "compressed": (not reused) and stored != result,
                                "dedup": reused,
                            }
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": stored,
                            }
                        )
                        reasons = list(ctx.task_state.stop_nudge_reasons)
                        final = force_stop_message(reasons)
                        log(f"[agent] {final}")
                        emit(
                            {
                                "type": "final",
                                "step": step,
                                "text": final,
                                "stopped_reason": "goal_met_forced",
                            }
                        )
                        emit({"type": "step_end", "step": step, "kind": "goal_met_forced"})
                        return _finish(
                            AgentResult(
                                final_text=final,
                                steps=step,
                                stopped_reason="goal_met_forced",
                                messages=messages,
                                memory=ctx.export_memory(),
                            )
                        )

                warn = loop_guard.warning_suffix(name, streak)
                if warn:
                    stored = f"{stored}{warn}"
                nudge = loop_guard.error_nudge_suffix(name, err_streak)
                if nudge and (not warn or "STOP" not in warn) and not hard_blocked:
                    stored = f"{stored}{nudge}"
                cycle_sfx = loop_guard.cycle_suffix(cycle_hit)
                if cycle_sfx and not hard_blocked:
                    # Prefer CYCLE_STOP over CYCLE_WARN text; skip if exact STOP already
                    if cycle_hit and cycle_hit.level == "stop":
                        stored = f"{stored}{cycle_sfx}"
                    elif not warn or "STOP" not in warn:
                        stored = f"{stored}{cycle_sfx}"
                stag_sfx = loop_guard.stagnation_suffix(name, obs_streak)
                if stag_sfx and not hard_blocked:
                    if "STAGNATION_STOP" in stag_sfx or (
                        not warn or "STOP" not in warn
                    ):
                        stored = f"{stored}{stag_sfx}"

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
                        "compressed": (not reused) and stored != result,
                        "dedup": reused,
                    }
                )
                if name == "todo_write" and not reused:
                    todos = _parse_todo_lines(result)
                    if todos is not None:
                        emit({"type": "todo_update", "step": step, "todos": todos})
                if (
                    ok
                    and not reused
                    and name in _MUTATING_FILE_TOOLS
                    and rel_path is not None
                ):
                    after_text = (
                        _snapshot_text(resolved_path) if resolved_path is not None else None
                    )
                    if after_text is None and isinstance(parsed, dict) and "content" in parsed:
                        after_text = str(parsed.get("content") or "")
                    emit(
                        {
                            "type": "file_change",
                            "step": step,
                            "id": call_id,
                            "tool": name,
                            "path": rel_path,
                            "old_content": _clip_diff(
                                before_text if before_text is not None else ""
                            ),
                            "new_content": _clip_diff(
                                after_text if after_text is not None else ""
                            ),
                            "is_new": before_text is None,
                        }
                    )
                    mutated_paths.append(rel_path.replace("\\", "/"))

                if ctx is not None:
                    emit(
                        {
                            "type": "task_state",
                            "step": step,
                            "task_state": ctx.task_state.to_dict(),
                            "retry_stage": ctx.task_state.retry_stage,
                            "failed_strategies": ctx.task_state.failed[:8],
                        }
                    )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": stored,
                    }
                )

                if hard_blocked:
                    final = (
                        f"(stopped: blocked exhausted strategy for `{name}` "
                        f"— retry_exhausted)"
                    )
                    log(f"[agent] {final}")
                    emit(
                        {
                            "type": "final",
                            "step": step,
                            "text": final,
                            "stopped_reason": "retry_exhausted",
                        }
                    )
                    emit({"type": "step_end", "step": step, "kind": "retry_exhausted"})
                    return _finish(
                        AgentResult(
                            final_text=final,
                            steps=step,
                            stopped_reason="retry_exhausted",
                            messages=messages,
                            memory=ctx.export_memory() if ctx is not None else None,
                        )
                    )

                if streak >= loop_guard.stop_after:
                    final = (
                        f"(stopped: identical tool `{name}` repeated {streak} times "
                        f"with the same arguments — loop_detected)"
                    )
                    log(f"[agent] {final}")
                    emit(
                        {
                            "type": "final",
                            "step": step,
                            "text": final,
                            "stopped_reason": "loop_detected",
                        }
                    )
                    emit({"type": "step_end", "step": step, "kind": "loop_detected"})
                    return _finish(
                        AgentResult(
                            final_text=final,
                            steps=step,
                            stopped_reason="loop_detected",
                            messages=messages,
                            memory=ctx.export_memory() if ctx is not None else None,
                        )
                    )

                if cycle_hit is not None and cycle_hit.level == "stop":
                    final = (
                        f"(stopped: alternating tool pattern period={cycle_hit.period} "
                        f"repeats={cycle_hit.repeats} — cycle_detected)"
                    )
                    log(f"[agent] {final}")
                    emit(
                        {
                            "type": "final",
                            "step": step,
                            "text": final,
                            "stopped_reason": "cycle_detected",
                        }
                    )
                    emit({"type": "step_end", "step": step, "kind": "cycle_detected"})
                    return _finish(
                        AgentResult(
                            final_text=final,
                            steps=step,
                            stopped_reason="cycle_detected",
                            messages=messages,
                            memory=ctx.export_memory() if ctx is not None else None,
                        )
                    )

                if (
                    loop_guard.stagnation_stop_after > 0
                    and obs_streak >= loop_guard.stagnation_stop_after
                ):
                    final = (
                        f"(stopped: same observation {obs_streak} times "
                        f"for `{name}` — stagnation_detected)"
                    )
                    log(f"[agent] {final}")
                    emit(
                        {
                            "type": "final",
                            "step": step,
                            "text": final,
                            "stopped_reason": "stagnation_detected",
                        }
                    )
                    emit({"type": "step_end", "step": step, "kind": "stagnation_detected"})
                    return _finish(
                        AgentResult(
                            final_text=final,
                            steps=step,
                            stopped_reason="stagnation_detected",
                            messages=messages,
                            memory=ctx.export_memory() if ctx is not None else None,
                        )
                    )

            # All tool results appended — safe to inject mid-run steers for next LLM turn
            _drain_steers(messages, step=step)

            # Soft stop: tests passed / todos done → urge FINAL (once)
            if ctx is not None and not ctx.task_state.final_nudge_sent:
                urge, reasons = evaluate_final_nudge(
                    task_state=ctx.task_state,
                    todos_text=ctx.state.todos_text,
                    step_had_failure=step_had_failure,
                )
                if urge:
                    nudge_msg = build_final_nudge_message(reasons, task_state=ctx.task_state)
                    messages.append({"role": "user", "content": nudge_msg})
                    ctx.task_state.final_nudge_sent = True
                    ctx.task_state.stop_nudge_reasons = list(reasons)
                    log(f"[stop_condition] final nudge: {', '.join(reasons)}")
                    emit(
                        {
                            "type": "final_nudge",
                            "step": step,
                            "reasons": reasons,
                            "text": nudge_msg,
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
        return _interrupt_result(step=0, messages=messages)
    except Exception as exc:
        emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        raise
