"""Read-only git inspection tools (Cap-B / C2).

No write/mutate git operations — status and diff only, cwd = workdir.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from src.agent.permissions import PermissionGate, SandboxError
from src.tools.base import FunctionTool, ToolRegistry
from src.tools.shell import _decode_output, _shell_env, _truncate


def _git_bin() -> str | None:
    return shutil.which("git")


def _run_git(
    gate: PermissionGate,
    argv: list[str],
    *,
    max_output_chars: int,
    timeout_sec: int = 30,
) -> str:
    git = _git_bin()
    if not git:
        return "Error: git executable not found on PATH"
    try:
        completed = subprocess.run(
            [git, *argv],
            shell=False,
            cwd=str(gate.workdir),
            capture_output=True,
            text=False,
            timeout=timeout_sec,
            env=_shell_env(),
        )
    except subprocess.TimeoutExpired:
        return f"Error: git timed out after {timeout_sec}s: git {' '.join(argv)}"
    except OSError as exc:
        return f"Error: failed to start git: {exc}"

    stdout = _decode_output(completed.stdout)
    stderr = _decode_output(completed.stderr)
    if completed.returncode != 0:
        err = (stderr or stdout or "").strip()
        if "not a git repository" in err.lower():
            return (
                "Error: workdir is not a git repository "
                f"({gate.workdir}). git_status/git_diff need a .git root."
            )
        return (
            f"Error: git {' '.join(argv)} failed (exit {completed.returncode})\n"
            f"{_truncate(err, max_output_chars)}"
        )
    body = stdout if stdout.strip() else "(clean / empty diff)"
    return _truncate(body, max_output_chars)


def register_git_tools(
    registry: ToolRegistry,
    gate: PermissionGate,
    *,
    max_output_chars: int = 8000,
) -> None:
    def git_status(args: dict[str, Any]) -> str:
        _ = args
        return _run_git(
            gate,
            ["status", "--short", "--branch"],
            max_output_chars=max_output_chars,
        )

    def git_diff(args: dict[str, Any]) -> str:
        staged = bool(args.get("staged", False))
        path = args.get("path")
        argv = ["diff", "--no-color"]
        if staged:
            argv.append("--staged")
        if path is not None:
            if not isinstance(path, str) or not path.strip():
                return "Error: path must be a non-empty string when provided"
            try:
                resolved = gate.resolve_path(path.strip())
                rel = resolved.relative_to(gate.workdir.resolve()).as_posix()
            except SandboxError as exc:
                return f"Error: {exc}"
            except Exception as exc:  # noqa: BLE001
                return f"Error: {exc}"
            argv.extend(["--", rel])
        return _run_git(gate, argv, max_output_chars=max_output_chars)

    registry.register(
        FunctionTool(
            name="git_status",
            description=(
                "Read-only: show git status --short --branch for the working directory. "
                "Use to see changed files before/after edits. Does not modify the repo."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            handler=git_status,
            risk_level="low",
            is_readonly=True,
        )
    )
    registry.register(
        FunctionTool(
            name="git_diff",
            description=(
                "Read-only: show git diff (unstaged by default; staged=true for index). "
                "Optional path limits to one file under workdir. Does not modify the repo."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional file path relative to workdir.",
                    },
                    "staged": {
                        "type": "boolean",
                        "description": "If true, show staged (index) diff.",
                    },
                },
                "required": [],
            },
            handler=git_diff,
            risk_level="low",
            is_readonly=True,
        )
    )
