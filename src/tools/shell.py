"""Shell tool: run_shell."""

from __future__ import annotations

import subprocess
from typing import Any

from src.agent.permissions import PermissionGate
from src.tools.base import FunctionTool, ToolRegistry


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, total {len(text)} chars]"


def register_shell_tools(
    registry: ToolRegistry,
    gate: PermissionGate,
    *,
    max_output_chars: int = 8000,
    timeout_sec: int = 60,
) -> None:
    def run_shell(args: dict[str, Any]) -> str:
        command = args.get("command")
        if not command or not isinstance(command, str):
            return "Error: missing required string argument 'command'"

        gate.check_shell(command)

        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(gate.workdir),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout_sec}s: {command!r}"
        except OSError as exc:
            return f"Error: failed to start shell: {exc}"

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        parts = [
            f"exit_code: {completed.returncode}",
            f"stdout:\n{_truncate(stdout, max_output_chars // 2) or '(empty)'}",
            f"stderr:\n{_truncate(stderr, max_output_chars // 2) or '(empty)'}",
        ]
        return "\n".join(parts)

    registry.register(
        FunctionTool(
            name="run_shell",
            description=(
                "Run a shell command inside the working directory. "
                "Use for running scripts, tests, package managers, etc. "
                "Prefer simple, non-interactive commands."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute (cwd = workdir).",
                    }
                },
                "required": ["command"],
            },
            handler=run_shell,
        )
    )
