"""Shell tool: run_shell."""

from __future__ import annotations

import os
import subprocess
from typing import Any

from src.agent.permissions import PermissionGate
from src.tools.base import FunctionTool, ToolRegistry


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, total {len(text)} chars]"


def _decode_output(data: bytes | None) -> str:
    """Decode subprocess bytes without crashing on Windows GBK/UTF-8 mix.

    ``text=True`` uses the system preferred encoding (often GBK on Chinese
    Windows). Pytest / Python often emit UTF-8, which then raises
    UnicodeDecodeError inside the subprocess reader thread.
    """
    if not data:
        return ""
    for encoding in ("utf-8", "gbk", "cp936"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _shell_env() -> dict[str, str]:
    """Prefer UTF-8 from child Python so stdout is predictable on Windows."""
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


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
            # bytes mode + manual decode: avoid GBK reader-thread crashes
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(gate.workdir),
                capture_output=True,
                text=False,
                timeout=timeout_sec,
                env=_shell_env(),
            )
        except subprocess.TimeoutExpired as exc:
            partial_out = _decode_output(exc.stdout)
            partial_err = _decode_output(exc.stderr)
            return (
                f"Error: command timed out after {timeout_sec}s: {command!r}\n"
                f"stdout:\n{_truncate(partial_out, max_output_chars // 2) or '(empty)'}\n"
                f"stderr:\n{_truncate(partial_err, max_output_chars // 2) or '(empty)'}"
            )
        except OSError as exc:
            return f"Error: failed to start shell: {exc}"

        stdout = _decode_output(completed.stdout)
        stderr = _decode_output(completed.stderr)
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
            risk_level="medium",
            is_readonly=False,
        )
    )
