"""Conversation / system-prompt helpers.

V1: build system prompt + optional workspace snapshot.
Day2: history trimming and smarter compaction can live here.
"""

from __future__ import annotations

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
4. Prefer read_file / list_dir before editing; use write_file to create or overwrite files.
5. Use run_shell to execute commands (tests, interpreters, etc.). Prefer non-interactive commands.
6. When the task is done, respond with a clear final answer and do NOT call more tools.
7. If a tool returns an error, explain briefly and try a different approach.

Current workdir top-level entries:
{snapshot}
"""


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, total {len(text)} chars]"
