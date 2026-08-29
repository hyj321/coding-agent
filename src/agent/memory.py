"""Project-level long-term memory via MEMORY.md (MemGPT-style editable block).

Short-term / working memory stays in ContextManager; this file is the archival
layer that persists across runs and is re-injected at the start of a new task.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MEMORY_CANDIDATES = ("MEMORY.md", ".agent/MEMORY.md")
MEMORY_HEADER = """# Project Memory

Cross-run notes for the coding agent (facts, conventions, pitfalls, what changed).
Edit freely; the agent appends a short entry after each completed run.
Newest entries are at the bottom; the agent prefers the recent tail on load.

---
"""


def resolve_memory_path(workdir: Path) -> Path:
    """Prefer an existing MEMORY.md / .agent/MEMORY.md; else default to workdir/MEMORY.md."""
    root = workdir.resolve()
    for name in MEMORY_CANDIDATES:
        path = root / name
        if path.is_file():
            return path
    agent_dir = root / ".agent"
    if agent_dir.is_dir():
        return agent_dir / "MEMORY.md"
    return root / "MEMORY.md"


def load_memory_excerpt(workdir: Path, *, max_chars: int = 3000) -> str:
    """Load MEMORY.md for prompt injection; prefer the recent tail when long."""
    path = resolve_memory_path(workdir)
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    text = text.strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    # Prefer recent entries (tail), keep a short head marker
    tail = text[-max_chars:]
    cut = tail.find("\n## ")
    if cut > 0 and cut < len(tail) // 2:
        tail = tail[cut + 1 :]
    return "…(earlier MEMORY.md truncated)…\n" + tail.lstrip()


def format_memory_section(excerpt: str) -> str:
    if not excerpt.strip():
        return ""
    return "## Project Memory (from MEMORY.md)\n" + excerpt.strip()


def append_run_to_memory(
    workdir: Path,
    *,
    task: str,
    final_text: str,
    stopped_reason: str,
    memory: dict[str, Any] | None = None,
    max_entry_chars: int = 1800,
) -> Path | None:
    """Append a rule-template entry (no LLM). Returns path written, or None on skip/error."""
    if stopped_reason == "interrupted":
        return None

    path = resolve_memory_path(workdir)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mem = memory or {}

    task_line = " ".join((task or "").strip().split())
    if len(task_line) > 120:
        task_line = task_line[:117] + "…"

    files = list(mem.get("focus_files") or [])[:8]
    errors = list(mem.get("last_errors") or [])[:3]
    history = str(mem.get("history_summary") or "").strip()
    todos = str(mem.get("todos_text") or "").strip()
    summary = " ".join((final_text or "").strip().split())
    if len(summary) > 400:
        summary = summary[:397] + "…"

    lines = [
        f"## [{stamp}] {task_line or '(no task)'}",
        f"- **Status:** {stopped_reason}",
    ]
    if files:
        lines.append("- **Files:** " + ", ".join(files))
    if errors:
        lines.append("- **Pitfalls / errors:**")
        for e in errors:
            lines.append(f"  - {e}")
    if summary:
        lines.append(f"- **Conclusion:** {summary}")
    if todos:
        # Keep todo block compact
        todo_flat = todos.replace("\n", " | ")
        if len(todo_flat) > 280:
            todo_flat = todo_flat[:277] + "…"
        lines.append(f"- **Todos:** {todo_flat}")
    if history:
        hist_lines = [ln.strip() for ln in history.splitlines() if ln.strip()][:6]
        if hist_lines:
            lines.append("- **Phase / history notes:**")
            for ln in hist_lines:
                if not ln.startswith("-"):
                    ln = "- " + ln
                lines.append(f"  {ln}")

    entry = "\n".join(lines).strip() + "\n"
    if len(entry) > max_entry_chars:
        entry = entry[: max_entry_chars - 20] + "\n…(entry truncated)\n"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            path.write_text(MEMORY_HEADER + "\n" + entry + "\n", encoding="utf-8")
        else:
            existing = path.read_text(encoding="utf-8")
            sep = "" if existing.endswith("\n") else "\n"
            path.write_text(existing + sep + "\n" + entry + "\n", encoding="utf-8")
    except OSError:
        return None
    return path
