"""Persist agent runs for debugging, demos, interviews, and session memory."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
        turns.append(
            {
                "created_at": stamp,
                "task": task,
                "stopped_reason": result.stopped_reason,
                "steps": result.steps,
                "final_text": result.final_text,
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
            for i, m in enumerate(result.messages):
                if m.get("role") == "user" and m.get("content") == task:
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
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    path = directory / f"run_{stamp}.json"
    payload = {
        "created_at": stamp,
        "task": task,
        "stopped_reason": result.stopped_reason,
        "steps": result.steps,
        "final_text": result.final_text,
        "meta": meta,
        "messages": result.messages,
        "memory": result.memory,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def append_session_file_changes(
    directory: Path,
    session_id: str,
    changes: list[dict[str, Any]],
) -> None:
    """Merge file_change records into the session transcript (for replay / memory)."""
    sid = _safe_session_id(session_id)
    if sid is None or not changes:
        return
    data = load_session(directory, sid)
    if data is None:
        return
    merged = list(data.get("file_changes") or [])
    by_path = {c.get("path"): i for i, c in enumerate(merged) if c.get("path")}
    for ch in changes:
        path = ch.get("path")
        if path and path in by_path:
            # Keep first old_content, latest new_content for cumulative view
            idx = by_path[path]
            old = merged[idx]
            merged[idx] = {
                **old,
                **ch,
                "old_content": old.get("old_content", ch.get("old_content")),
                "new_content": ch.get("new_content"),
            }
        else:
            if path:
                by_path[path] = len(merged)
            merged.append(ch)
    data["file_changes"] = merged
    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = session_path(directory, sid)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


_CONTEXT_MARKERS = (
    "[Context Manager — layered working memory]",
    "[system note]",
    "[Session memory",
)


def _is_ephemeral_user_note(content: Any) -> bool:
    if not isinstance(content, str):
        return False
    return any(content.startswith(m) for m in _CONTEXT_MARKERS)


def build_continue_context(
    session: dict[str, Any],
    *,
    recent_k: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Slim prior context for Web multi-turn continue (P0).

    Returns (prior_messages, prior_memory) where prior_messages is:
      original task + optional memory hint + recent K messages (tool-pair aware),
    instead of the full session dump.
    """
    memory = session.get("memory")
    if memory is not None and not isinstance(memory, dict):
        memory = None

    all_msgs = [m for m in (session.get("messages") or []) if m.get("role") != "system"]
    original_task = str(session.get("task") or "").strip()

    # Find first real user task in messages if session.task missing
    first_user: dict[str, Any] | None = None
    for m in all_msgs:
        if m.get("role") == "user" and not _is_ephemeral_user_note(m.get("content")):
            first_user = {"role": "user", "content": m.get("content")}
            if not original_task and isinstance(m.get("content"), str):
                original_task = m["content"]
            break

    prior: list[dict[str, Any]] = []
    if original_task:
        prior.append({"role": "user", "content": original_task})
    elif first_user is not None:
        prior.append(first_user)

    # Compact memory snapshot as an explicit note (working memory hydrate is separate)
    if memory:
        focus = memory.get("focus_files") or []
        errors = memory.get("last_errors") or []
        hist = str(memory.get("history_summary") or "").strip()
        todos = str(memory.get("todos_text") or "").strip()
        lines = ["[Session memory snapshot from previous turn]"]
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

    # Recent tail, skipping ephemeral context notes; keep tool pairing
    k = max(4, recent_k)
    candidates = [
        m
        for m in all_msgs
        if not (m.get("role") == "user" and _is_ephemeral_user_note(m.get("content")))
    ]
    # Drop the original first user task from the pool so we don't duplicate it in the tail
    if candidates and candidates[0].get("role") == "user":
        candidates = candidates[1:]

    tail = candidates[-k:] if len(candidates) > k else list(candidates)
    while tail and tail[0].get("role") == "tool":
        # Expand left to include the assistant tool_calls message
        start = len(candidates) - len(tail) - 1
        if start < 0:
            tail = tail[1:]
            break
        tail = candidates[start:]
        if tail[0].get("role") != "tool":
            break

    prior.extend(tail)
    return prior, memory
