"""Permission / sandbox helpers.

- Path sandbox: file ops must stay under workdir
- Shell policy: hard-deny patterns + approval modes (auto | ask | never)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class SandboxError(Exception):
    """Raised when a tool action violates the sandbox / policy."""


class ApprovalMode(str, Enum):
    AUTO = "auto"  # allow (except hard-deny)
    ASK = "ask"  # prompt on risky / mutating actions
    NEVER = "never"  # deny risky without prompting; allow safe ops


AskFn = Callable[[str], bool]


# Always blocked, regardless of approval mode.
_HARD_DENY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"rm\s+-rf\s+[/\\](\s|$)", re.I),
    re.compile(r"rm\s+-rf\s+~", re.I),
    re.compile(r"del\s+/[fqs].*\*", re.I),
    re.compile(r"format\s+[a-z]:", re.I),
    re.compile(r"mkfs\.", re.I),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;", re.I),  # fork bomb
    re.compile(r"shutdown(\s|$)", re.I),
    re.compile(r"reboot(\s|$)", re.I),
]

# Risky but may be intentional — subject to ask/never.
_RISKY_SHELL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-rf\b", re.I),
    re.compile(r"\bgit\s+push\b.*--force", re.I),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.I),
    re.compile(r"\bsudo\b", re.I),
    re.compile(r"\bcurl\b.*\|\s*(ba)?sh", re.I),
    re.compile(r"\biwr\b.*\|\s*iex", re.I),
    re.compile(r"Remove-Item\s+.*-Recurse", re.I),
    re.compile(r"\breg\s+delete\b", re.I),
]

_MUTATING_TOOLS = frozenset({"write_file", "edit_file", "run_shell"})


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    reason: str = ""


def _default_ask(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


class PermissionGate:
    """Central gate for path resolution and tool authorization."""

    def __init__(
        self,
        workdir: Path,
        *,
        approval: ApprovalMode = ApprovalMode.AUTO,
        ask_fn: AskFn | None = None,
    ) -> None:
        self.workdir = workdir.resolve()
        self.approval = approval
        self.ask_fn = ask_fn or _default_ask

    def resolve_path(self, user_path: str | Path) -> Path:
        """Resolve a user-supplied path and ensure it stays under workdir."""
        raw = Path(user_path)
        if not raw.is_absolute():
            candidate = (self.workdir / raw).resolve()
        else:
            candidate = raw.resolve()

        try:
            candidate.relative_to(self.workdir)
        except ValueError as exc:
            raise SandboxError(
                f"Path escapes workdir sandbox: {user_path!r} -> {candidate} "
                f"(workdir={self.workdir})"
            ) from exc
        return candidate

    def authorize(self, tool_name: str, arguments: dict[str, Any]) -> AuthDecision:
        """Decide whether a tool call may run. Called by the agent loop before dispatch."""
        if tool_name == "run_shell":
            command = str(arguments.get("command") or "")
            for pat in _HARD_DENY_PATTERNS:
                if pat.search(command):
                    return AuthDecision(False, f"hard-denied dangerous shell pattern: {pat.pattern}")

            risky = any(p.search(command) for p in _RISKY_SHELL_PATTERNS)
            if risky:
                if self.approval == ApprovalMode.AUTO:
                    return AuthDecision(True, "risky shell allowed (approval=auto)")
                if self.approval == ApprovalMode.NEVER:
                    return AuthDecision(False, "risky shell denied (approval=never)")
                prompt = f"[approval] allow risky shell?\n  {command}"
                if self.ask_fn(prompt):
                    return AuthDecision(True, "user approved risky shell")
                return AuthDecision(False, "user denied risky shell")

        if tool_name in _MUTATING_TOOLS and self.approval == ApprovalMode.ASK:
            summary = _brief_args(tool_name, arguments)
            prompt = f"[approval] allow {tool_name}?\n  {summary}"
            if not self.ask_fn(prompt):
                return AuthDecision(False, f"user denied {tool_name}")
            return AuthDecision(True, f"user approved {tool_name}")

        return AuthDecision(True, "ok")

    def check_shell(self, command: str) -> None:
        """Legacy hook for shell tool; prefer authorize() in the loop."""
        decision = self.authorize("run_shell", {"command": command})
        if not decision.allowed:
            raise SandboxError(decision.reason)


def _brief_args(tool_name: str, arguments: dict[str, Any], limit: int = 160) -> str:
    if tool_name == "run_shell":
        text = str(arguments.get("command", ""))
    elif tool_name in {"write_file", "edit_file", "read_file"}:
        text = str(arguments.get("path", ""))
    else:
        text = str(arguments)
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + "..."


def parse_approval_mode(value: str | None) -> ApprovalMode:
    if not value:
        return ApprovalMode.AUTO
    normalized = value.strip().lower()
    try:
        return ApprovalMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(m.value for m in ApprovalMode)
        raise ValueError(f"Invalid approval mode {value!r}. Expected one of: {allowed}") from exc
