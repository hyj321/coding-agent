"""Dedicated test runner tool (Cap-B / C3).

Prefer ``run_tests`` over free-form ``run_shell`` for verification so TaskState
and CompletionGate get structured exit/summary without shell guessing.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.agent.permissions import PermissionGate, SandboxError
from src.tools.base import FunctionTool, ToolRegistry
from src.tools.shell import _decode_output, _shell_env, _truncate


def _pytest_available() -> bool:
    try:
        return importlib.util.find_spec("pytest") is not None
    except (ImportError, ValueError):
        return False


def _build_argv(
    gate: PermissionGate,
    *,
    target: str,
    runner: str,
) -> tuple[list[str], str, Path]:
    """Return (argv, label, resolved_path). Raises ValueError on bad args."""
    target = (target or ".").strip() or "."
    runner = (runner or "auto").strip().lower()
    if runner not in {"auto", "pytest", "python", "unittest"}:
        raise ValueError(
            f"Error: runner must be auto|pytest|python|unittest, got {runner!r}"
        )

    resolved = gate.resolve_path(target)
    if not resolved.exists():
        raise ValueError(f"Error: test target not found: {target}")

    rel = resolved.relative_to(gate.workdir).as_posix()
    py = sys.executable

    if runner == "python" or (
        runner == "auto" and resolved.is_file() and resolved.suffix.lower() == ".py"
    ):
        if not resolved.is_file() or resolved.suffix.lower() != ".py":
            raise ValueError(
                f"Error: runner=python requires a .py file, got {target!r}"
            )
        return [py, str(resolved)], f"python {rel}", resolved

    if runner == "unittest":
        # Discover under a package/dir, or run a module path
        if resolved.is_file():
            mod = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel
            return (
                [py, "-m", "unittest", mod, "-v"],
                f"unittest {mod}",
                resolved,
            )
        return (
            [py, "-m", "unittest", "discover", "-s", str(resolved), "-v"],
            f"unittest discover {rel}",
            resolved,
        )

    # pytest (explicit or auto for dirs / non-script)
    if runner == "pytest" or runner == "auto":
        if not _pytest_available() and runner == "pytest":
            raise ValueError(
                "Error: pytest is not installed; use runner=python on a *_test.py "
                "or install pytest."
            )
        if not _pytest_available() and runner == "auto":
            # Fall back: if a single test-like file, run with python
            if resolved.is_file() and resolved.suffix.lower() == ".py":
                return [py, str(resolved)], f"python {rel}", resolved
            raise ValueError(
                "Error: pytest not installed and target is not a .py file. "
                "Pass target='some_test.py' with runner=python, or install pytest."
            )
        argv = [py, "-m", "pytest", str(resolved), "-q", "--tb=short"]
        return argv, f"pytest {rel}", resolved

    raise ValueError(f"Error: unhandled runner {runner!r}")


def register_testing_tools(
    registry: ToolRegistry,
    gate: PermissionGate,
    *,
    max_output_chars: int = 8000,
    timeout_sec: int = 90,
) -> None:
    def run_tests(args: dict[str, Any]) -> str:
        target = args.get("target")
        if target is not None and not isinstance(target, str):
            return "Error: target must be a string path"
        runner = args.get("runner") or "auto"
        if not isinstance(runner, str):
            return "Error: runner must be a string"

        try:
            argv, label, _resolved = _build_argv(
                gate, target=str(target or "."), runner=runner
            )
        except SandboxError as exc:
            return f"Error: {exc}"
        except (ValueError, OSError) as exc:
            msg = str(exc)
            return msg if msg.startswith("Error:") else f"Error: {exc}"

        try:
            completed = subprocess.run(
                argv,
                shell=False,
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
                f"Error: run_tests timed out after {timeout_sec}s: {label}\n"
                f"exit_code: -1\n"
                f"stdout:\n{_truncate(partial_out, max_output_chars // 2) or '(empty)'}\n"
                f"stderr:\n{_truncate(partial_err, max_output_chars // 2) or '(empty)'}"
            )
        except OSError as exc:
            return f"Error: failed to start run_tests: {exc}"

        stdout = _decode_output(completed.stdout)
        stderr = _decode_output(completed.stderr)
        code = int(completed.returncode)
        passed = code == 0
        header = [
            f"# run_tests {label}",
            f"passed: {'true' if passed else 'false'}",
            f"exit_code: {code}",
            f"stdout:\n{_truncate(stdout, max_output_chars // 2) or '(empty)'}",
            f"stderr:\n{_truncate(stderr, max_output_chars // 2) or '(empty)'}",
        ]
        return "\n".join(header)

    registry.register(
        FunctionTool(
            name="run_tests",
            description=(
                "Run tests under the working directory with a structured summary "
                "(exit_code + passed). Prefer this over run_shell for pytest / "
                "*_test.py verification. "
                "runner=auto: .py file → python; else pytest if installed. "
                "Does not accept free-form shell commands."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "Test file, directory, or '.' relative to workdir. "
                            "Example: greeter_test.py"
                        ),
                    },
                    "runner": {
                        "type": "string",
                        "description": "auto | pytest | python | unittest (default auto).",
                        "enum": ["auto", "pytest", "python", "unittest"],
                    },
                },
                "required": [],
            },
            handler=run_tests,
            risk_level="medium",
            is_readonly=True,
        )
    )
