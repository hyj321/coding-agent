"""Persist agent runs for debugging, demos, interviews, and session memory."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agent.context_manager import expand_tail_for_tool_pairing, sanitize_tool_pairing
from src.agent.loop import AgentResult

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")


def _safe_session_id(session_id: str | None) -> str | None:
    if not session_id:
        return None
    sid = session_id.strip()
    if not _SESSION_ID_RE.match(sid):
        return None
    return sid


def session_path(directory: Path, session_id: str) -> Path:
    return directory / f"session_{session_id}.json"


def load_session(directory: Path, session_id: str) -> dict[str, Any] | None:
    sid = _safe_session_id(session_id)
    if sid is None:
        return None
    path = session_path(directory, sid)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_transcript(
    directory: Path,
    *,
    task: str,
    result: AgentResult,
    meta: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> Path:
    """Save one agent turn.

    If session_id is provided, append/update a single session file so that
    one conversation (= one history item) can feed long/short-term memory later.
    Otherwise write a legacy run_*.json snapshot.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    meta = dict(meta or {})
    sid = _safe_session_id(session_id)

    if sid is not None:
        path = session_path(directory, sid)
        existing = load_session(directory, sid) or {
            "created_at": stamp,
            "session_id": sid,
            "kind": "session",
            "turns": [],
            "task": task,
            "messages": [],
            "file_changes": [],
        }
        turns = list(existing.get("turns") or [])
        turn_usage = None
        turn_cost = None
        if isinstance(result.memory, dict):
            cu = result.memory.get("context_usage")
            if isinstance(cu, dict):
                turn_usage = cu
            cr = result.memory.get("cost_report")
            if isinstance(cr, dict):
                turn_cost = cr
        turns.append(
            {
                "created_at": stamp,
                "task": task,
                "stopped_reason": result.stopped_reason,
                "steps": result.steps,
                "final_text": result.final_text,
                "summary": (result.memory or {}).get("turn_summary")
                if isinstance(result.memory, dict)
                else None,
                "context_usage": turn_usage,
                "cost_report": turn_cost,
            }
        )
        title = str(existing.get("task") or task)
        prev_msgs = list(existing.get("messages") or [])
        # Episodic full history: append only this turn's new user task + following msgs.
        # Model continue uses build_continue_context() to slim; disk keeps the diary.
        if not prev_msgs:
            merged_messages = list(result.messages)
        else:
            cut = 0
            resolved = meta.get("resolved_task")
            for i, m in enumerate(result.messages):
                if m.get("role") != "user":
                    continue
                content = m.get("content")
                if content == task or (resolved and content == resolved):
                    cut = i
                    break
            else:
                # Continue-expand / mismatch: take the last user message in this run
                for i in range(len(result.messages) - 1, -1, -1):
                    if result.messages[i].get("role") == "user":
                        cut = i
                        break
            merged_messages = prev_msgs + list(result.messages[cut:])

        payload = {
            "created_at": existing.get("created_at") or stamp,
            "updated_at": stamp,
            "session_id": sid,
            "kind": "session",
            "task": title,
            "stopped_reason": result.stopped_reason,
            "steps": result.steps,
            "final_text": result.final_text,
            "meta": {**(existing.get("meta") or {}), **meta, "session_id": sid},
            "turns": turns,
            "messages": merged_messages,
            "file_changes": existing.get("file_changes") or [],
            "memory": result.memory,
            "context_usage": turn_usage,
            "cost_report": turn_cost,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    path = directory / f"run_{stamp}.json"
    turn_usage = None
    turn_cost = None
    if isinstance(result.memory, dict):
        cu = result.memory.get("context_usage")
        if isinstance(cu, dict):
            turn_usage = cu
        cr = result.memory.get("cost_report")
        if isinstance(cr, dict):
            turn_cost = cr
    payload = {
        "created_at": stamp,
        "task": task,
        "stopped_reason": result.stopped_reason,
        "steps": result.steps,
        "final_text": result.final_text,
        "meta": meta,
        "messages": result.messages,
        "memory": result.memory,
        "context_usage": turn_usage,
        "cost_report": turn_cost,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def append_session_file_changes(
    directory: Path,
    session_id: str,
    changes: list[dict[str, Any]],
) -> None:
    """Record file_change events for the *latest turn* only (UI shows this turn)."""
    sid = _safe_session_id(session_id)
    if sid is None or not changes:
        return
    data = load_session(directory, sid)
    if data is None:
        return

    # Dedupe by path within this turn: keep first old_content, latest new_content
    merged: list[dict[str, Any]] = []
    by_path: dict[str, int] = {}
    for ch in changes:
        path = ch.get("path")
        if not path:
            merged.append(ch)
            continue
        if path in by_path:
            idx = by_path[path]
            old = merged[idx]
            merged[idx] = {
                **old,
                **ch,
                "old_content": old.get("old_content", ch.get("old_content")),
                "new_content": ch.get("new_content"),
                "is_new": bool(old.get("is_new") or ch.get("is_new")),
            }
        else:
            by_path[path] = len(merged)
            merged.append(dict(ch))

    turns = list(data.get("turns") or [])
    if turns:
        turns[-1] = {**turns[-1], "file_changes": merged}
        data["turns"] = turns

    # Top-level list = current (last) turn only — not cumulative across the session
    data["file_changes"] = merged
    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = session_path(directory, sid)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


_CONTEXT_MARKERS = (
    "[Context Manager — layered working memory]",
    "[Root State — re-injected after context fold]",
    "[system note]",
    "[Session memory",
    "[Continue briefing]",
)

_CONTINUE_PHRASES = frozenset(
    {
        "继续",
        "继续做",
        "接着做",
        "接着改",
        "继续改",
        "继续完成",
        "往下做",
        "再继续",
        "continue",
        "keep going",
        "resume",
    }
)


def _is_ephemeral_user_note(content: Any) -> bool:
    if not isinstance(content, str):
        return False
    return any(content.startswith(m) for m in _CONTEXT_MARKERS)


def _normalize_continue_text(text: str) -> str:
    t = text.strip().lower()
    for ch in "。.！!？?~～…":
        t = t.replace(ch, "")
    return t.strip()


def is_continue_phrase(text: str) -> bool:
    """True for short continue utterances without a new goal."""
    raw = (text or "").strip()
    if not raw or len(raw) > 40:
        return False
    return _normalize_continue_text(raw) in _CONTINUE_PHRASES


def last_active_task(session: dict[str, Any]) -> str:
    """Prefer the latest unfinished turn task, else the latest turn task."""
    turns = list(session.get("turns") or [])
    unfinished = [
        t
        for t in turns
        if str(t.get("stopped_reason") or "") in {"max_steps", "interrupted"}
        and str(t.get("task") or "").strip()
        and not is_continue_phrase(str(t.get("task") or ""))
    ]
    if unfinished:
        return str(unfinished[-1].get("task") or "").strip()
    for t in reversed(turns):
        task = str(t.get("task") or "").strip()
        if task and not is_continue_phrase(task):
            return task
    for m in reversed(session.get("messages") or []):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str) and content.strip() and not _is_ephemeral_user_note(content):
            if not is_continue_phrase(content):
                return content.strip()
    return str(session.get("task") or "").strip()


def resolve_continue_task(user_task: str, session: dict[str, Any] | None) -> str:
    """Expand continue phrases into an explicit goal (avoid reviving turn-0)."""
    text = (user_task or "").strip()
    if not session or not is_continue_phrase(text):
        return text
    active = last_active_task(session)
    if not active:
        return text
    turns = list(session.get("turns") or [])
    unfinished = [
        t
        for t in turns
        if str(t.get("stopped_reason") or "") in {"max_steps", "interrupted"}
        and str(t.get("task") or "").strip() == active
    ]
    reason = str(unfinished[-1].get("stopped_reason") or "") if unfinished else ""
    if not reason and turns:
        reason = str(turns[-1].get("stopped_reason") or "")
    if reason == "max_steps":
        return (
            f"继续完成上一个未完成的任务（刚才因达到 max_steps 中断）：{active}\n"
            "不要切换到更早的其它任务；从已有改动基础上接着做，做完再验证。"
        )
    if reason == "interrupted":
        return (
            f"继续完成上一个被中断的任务：{active}\n"
            "不要切换到更早的其它任务；从已有进度接着做。"
        )
    return (
        f"继续围绕上一任务推进：{active}\n"
        "若上一任务已完成，做合理的小改进或确认验证；不要回到会话最初的无关任务。"
    )


def build_continue_context(
    session: dict[str, Any],
    *,
    recent_k: int = 24,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Slim prior context for Web multi-turn continue.

    Emphasizes the *latest* active goal — never treat turn-0 as the only goal.
    """
    memory = session.get("memory")
    if memory is not None and not isinstance(memory, dict):
        memory = None

    all_msgs = [m for m in (session.get("messages") or []) if m.get("role") != "system"]
    original_task = str(session.get("task") or "").strip()
    active_task = last_active_task(session) or original_task

    prior: list[dict[str, Any]] = []
    turns = list(session.get("turns") or [])
    brief_lines = ["[Continue briefing]"]
    if original_task and original_task != active_task:
        brief_lines.append(f"Session started with: {original_task}")
    if active_task:
        brief_lines.append(f"ACTIVE GOAL (continue this): {active_task}")
    if turns:
        last = turns[-1]
        brief_lines.append(
            "Last turn: "
            f"task={last.get('task')!r} "
            f"stopped={last.get('stopped_reason')} "
            f"steps={last.get('steps')}"
        )
        if str(last.get("stopped_reason")) == "max_steps":
            brief_lines.append(
                "Previous turn hit max_steps without finishing — resume that work."
            )
    if len(turns) > 1:
        brief_lines.append("Recent turns:")
        for t in turns[-4:]:
            brief_lines.append(
                f"- ({t.get('stopped_reason')}) {str(t.get('task') or '')[:80]}"
            )
    prior.append({"role": "user", "content": "\n".join(brief_lines)})

    if memory:
        focus = memory.get("focus_files") or []
        errors = memory.get("last_errors") or []
        hist = str(memory.get("history_summary") or "").strip()
        todos = str(memory.get("todos_text") or "").strip()
        lines = ["[Session memory snapshot from previous turn]"]
        if active_task:
            lines.append(f"Active task override: {active_task}")
        if focus:
            lines.append("Focus files: " + ", ".join(str(f) for f in focus[:8]))
        if errors:
            lines.append("Recent errors:")
            for e in errors[:3]:
                lines.append(f"- {e}")
        if todos:
            lines.append(todos if len(todos) <= 500 else todos[:497] + "…")
        if hist:
            hist_short = hist if len(hist) <= 800 else hist[-800:]
            lines.append("Historical Context:")
            lines.append(hist_short)
        prior.append({"role": "user", "content": "\n".join(lines)})
        memory = dict(memory)
        if active_task:
            memory["task"] = active_task

    k = max(8, recent_k)
    candidates = [
        m
        for m in all_msgs
        if not (m.get("role") == "user" and _is_ephemeral_user_note(m.get("content")))
    ]

    last_goal_idx = -1
    for i in range(len(candidates) - 1, -1, -1):
        m = candidates[i]
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        if is_continue_phrase(content):
            continue
        last_goal_idx = i
        break

    tail = candidates[-k:] if len(candidates) > k else list(candidates)
    if last_goal_idx >= 0:
        start = len(candidates) - len(tail)
        if last_goal_idx < start:
            tail = candidates[last_goal_idx:]
            if len(tail) > 40:
                head = candidates[last_goal_idx]
                rest = candidates[-39:]
                tail = [head] + [m for m in rest if m is not head]

    tail = expand_tail_for_tool_pairing(candidates, list(tail), min_index=0)
    prior.extend(tail)
    return sanitize_tool_pairing(prior), memory
