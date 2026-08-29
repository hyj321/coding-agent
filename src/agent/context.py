"""Conversation / system-prompt helpers + history trimming."""

from __future__ import annotations

from typing import Any
from pathlib import Path

from src.tools.filesystem import snapshot_workdir


def build_system_prompt(workdir: Path, tool_names: list[str]) -> str:
    snapshot = snapshot_workdir(workdir)
    tools_line = ", ".join(tool_names)
    return f"""You are a coding agent running on the user's machine.
You solve programming tasks by calling tools. Important rules:

1. The working directory is: {workdir}
2. All file paths are relative to that directory unless stated otherwise.
3. Available tools: {tools_line}
4. Prefer read_file / list_dir / glob before editing.
5. For small edits, prefer edit_file (exact string replace) over rewriting the whole file with write_file.
6. Use write_file to create new files or for large rewrites.
7. Use run_shell for tests and scripts. Prefer non-interactive commands.
8. Plan-then-Act (important): for any non-trivial task (more than one step), FIRST call todo_write
   with a short checklist, keep exactly one item in_progress, update the list as you go, and
   mark items completed. Do not give the final answer until the checklist is done (or cancelled).
9. When the task is done, respond with a clear final answer and do NOT call more tools.
10. If a tool returns an error, explain briefly and try a different approach.

Current workdir top-level entries:
{snapshot}
"""


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, total {len(text)} chars]"


def trim_messages(
    messages: list[dict[str, Any]],
    *,
    max_messages: int,
) -> list[dict[str, Any]]:
    """Keep system + original user task, plus a recent tail that respects tool pairing.

    Does not mutate the caller's list. If already within budget, returns as-is.
    """
    if max_messages < 4 or len(messages) <= max_messages:
        return messages

    head = messages[:2]  # system + first user
    budget = max_messages - len(head)
    if budget < 2:
        budget = 2

    tail = messages[-budget:]
    # Drop leading orphan tool messages (must follow an assistant tool_calls turn).
    while tail and tail[0].get("role") == "tool":
        tail = tail[1:]

    # If the first assistant in tail has tool_calls, ensure all matching tool results stay.
    # (Budget may already include them; if we stripped too aggressively, expand backward.)
    if not tail:
        return list(messages[:max_messages])

    start_idx = len(messages) - len(tail)
    while start_idx > 2 and tail and tail[0].get("role") == "tool":
        start_idx -= 1
        tail = messages[start_idx:]

    trimmed = head + tail
    if len(trimmed) == len(messages):
        return messages

    notice = {
        "role": "user",
        "content": (
            "[system note] Earlier conversation turns were trimmed to fit the context "
            "budget. Continue from the recent tool results and the original task."
        ),
    }
    # Insert notice after head if room; otherwise just return trimmed head+tail.
    if len(trimmed) + 1 <= max_messages:
        return head + [notice] + tail
    return trimmed
