"""Permission / sandbox helpers.

Pipeline: Permission Manager → Risk Assessment → Allow / Confirm / Deny → Tool

- Path sandbox: file ops must stay under workdir
- Sensitive paths: always deny (.env, keys, .ssh, …)
- Tool metadata: risk_level (low|medium|high) + is_readonly from Registry
- Shell policy: hard-deny patterns + arg heuristics that may raise risk
- Approval modes: auto | ask | never
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal

RiskLevel = Literal["low", "medium", "high"]

_RISK_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


class SandboxError(Exception):
    """Raised when a tool action violates the sandbox / policy."""


class ApprovalMode(str, Enum):
    AUTO = "auto"  # allow (except hard-deny / sensitive)
    ASK = "ask"  # prompt on medium/high
    NEVER = "never"  # deny medium/high without prompting; allow low


@dataclass(frozen=True)
class ApprovalPrompt:
    """Structured ask payload (CLI uses str(prompt); Web reads fields)."""

    tool_name: str
    risk_level: RiskLevel
    summary: str
    arguments: dict[str, Any]
    call_id: str | None = None

    def __str__(self) -> str:
        return (
            f"[approval] allow {self.tool_name} ({self.risk_level} risk)?\n"
            f"  {self.summary}"
        )


AskFn = Callable[[ApprovalPrompt], bool]


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

# High-risk shell — subject to ask/never (and labels the call as high).
_HIGH_SHELL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-rf\b", re.I),
    re.compile(r"\bgit\s+push\b.*--force", re.I),
    re.compile(r"\bgit\s+push\b.*\s-f(\s|$)", re.I),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.I),
    re.compile(r"\bsudo\b", re.I),
    re.compile(r"\bcurl\b.*\|\s*(ba)?sh", re.I),
    re.compile(r"\bwget\b.*\|\s*(ba)?sh", re.I),
    re.compile(r"\biwr\b.*\|\s*iex", re.I),
    re.compile(r"Remove-Item\s+.*-Recurse", re.I),
    re.compile(r"\breg\s+delete\b", re.I),
]

# Basename / path patterns that must never be read or written via tools.
_SENSITIVE_BASENAME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\.env$", re.I),
    re.compile(r"^\.env\..+", re.I),
    re.compile(r".*\.pem$", re.I),
    re.compile(r".*\.key$", re.I),
    re.compile(r"^id_rsa(\b|\.|$)", re.I),
    re.compile(r"^id_ed25519(\b|\.|$)", re.I),
    re.compile(r"^id_dsa(\b|\.|$)", re.I),
    re.compile(r".*\.p12$", re.I),
    re.compile(r"^credentials\.json$", re.I),
]

_SENSITIVE_DIR_NAMES = frozenset({".ssh", "secrets"})

# Shell commands that print a file — check their path argument for secrets.
_SHELL_READ_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:cat|type|Get-Content|gc)\s+(?:-[a-zA-Z]+\s+)*[\"']?([^\s\"'|;&>]+)",
        re.I,
    ),
]


@dataclass(frozen=True)
class AuthDecision:
    allowed: bool
    reason: str = ""
    risk_level: RiskLevel = "low"


def _default_ask(prompt: ApprovalPrompt | str) -> bool:
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _max_risk(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    return a if _RISK_RANK[a] >= _RISK_RANK[b] else b


def is_sensitive_path(user_path: str | Path) -> bool:
    """Return True if the path looks like credentials / secrets."""
    try:
        parts = Path(user_path).parts
    except (TypeError, ValueError):
        return False
    if not parts:
        return False
    for part in parts[:-1]:
        if part.lower() in _SENSITIVE_DIR_NAMES or part == ".ssh":
            return True
    name = parts[-1]
    return any(p.search(name) for p in _SENSITIVE_BASENAME_PATTERNS)


def _shell_sensitive_targets(command: str) -> list[str]:
    hits: list[str] = []
    for pat in _SHELL_READ_PATTERNS:
        for match in pat.finditer(command):
            target = match.group(1).strip()
            if target and is_sensitive_path(target):
                hits.append(target)
    return hits


def assess_shell_risk(command: str) -> tuple[RiskLevel, str | None]:
    """Return (risk, hard_deny_reason). hard_deny_reason set ⇒ must Deny."""
    for pat in _HARD_DENY_PATTERNS:
        if pat.search(command):
            return "high", f"hard-denied dangerous shell pattern: {pat.pattern}"
    sensitive = _shell_sensitive_targets(command)
    if sensitive:
        return "high", f"已拒绝 shell 中的敏感路径 denied sensitive path in shell command: {sensitive[0]!r}"
    if any(p.search(command) for p in _HIGH_SHELL_PATTERNS):
        return "high", None
    return "medium", None


class PermissionGate:
    """Central gate for path resolution and tool authorization."""

    def __init__(
        self,
        workdir: Path,
        *,
        approval: ApprovalMode = ApprovalMode.AUTO,
        ask_fn: AskFn | None = None,
        deny_high: bool = False,
        ask_min_risk: RiskLevel = "medium",
    ) -> None:
        self.workdir = workdir.resolve()
        self.approval = approval
        self.ask_fn = ask_fn or _default_ask
        self.deny_high = deny_high
        self.ask_min_risk: RiskLevel = (
            ask_min_risk if ask_min_risk in _RISK_RANK else "medium"
        )
        self._registry: Any | None = None

    def bind_registry(self, registry: Any) -> None:
        """Attach ToolRegistry so authorize() can read risk_level / is_readonly."""
        self._registry = registry

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

    def _tool_meta(self, tool_name: str) -> tuple[RiskLevel, bool]:
        """Return (risk_level, is_readonly) from registry metadata."""
        if self._registry is None:
            return "medium", False
        tool = self._registry.get(tool_name)
        if tool is None:
            return "medium", False
        risk = getattr(tool, "risk_level", "medium")
        if risk not in _RISK_RANK:
            risk = "medium"
        readonly = bool(getattr(tool, "is_readonly", False))
        return risk, readonly  # type: ignore[return-value]

    def assess_risk(self, tool_name: str, arguments: dict[str, Any]) -> tuple[RiskLevel, str | None]:
        """Compute effective risk and optional hard-deny reason."""
        base_risk, _readonly = self._tool_meta(tool_name)

        # Path-bearing tools: sensitive files are always denied.
        if tool_name in {"read_file", "write_file", "edit_file"}:
            path_arg = arguments.get("path")
            if path_arg is not None and is_sensitive_path(str(path_arg)):
                return "high", f"已拒绝敏感路径 denied sensitive path: {path_arg!r}"

        if tool_name == "run_shell":
            command = str(arguments.get("command") or "")
            shell_risk, shell_deny = assess_shell_risk(command)
            if shell_deny:
                return "high", shell_deny
            return _max_risk(base_risk, shell_risk), None

        return base_risk, None

    def authorize(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        call_id: str | None = None,
    ) -> AuthDecision:
        """Decide whether a tool call may run. Called by the agent loop before dispatch."""
        risk, deny_reason = self.assess_risk(tool_name, arguments)
        if deny_reason:
            return AuthDecision(False, deny_reason, risk_level=risk)

        if risk == "low":
            return AuthDecision(True, "ok (low risk)", risk_level=risk)

        # High: optional hard deny (legacy Web) even under approval=auto
        if risk == "high" and self.deny_high:
            return AuthDecision(
                False,
                "高风险已拒绝 high risk denied (deny_high=true)",
                risk_level=risk,
            )

        # medium / high
        if self.approval == ApprovalMode.AUTO:
            return AuthDecision(
                True,
                f"{risk} risk allowed (approval=auto)",
                risk_level=risk,
            )
        if self.approval == ApprovalMode.NEVER:
            return AuthDecision(
                False,
                f"{risk} risk denied (approval=never)",
                risk_level=risk,
            )

        # ASK — only prompt when risk >= ask_min_risk (Web default: medium+)
        if _RISK_RANK[risk] < _RISK_RANK[self.ask_min_risk]:
            return AuthDecision(
                True,
                f"{risk} risk auto-allowed (ask_min_risk={self.ask_min_risk})",
                risk_level=risk,
            )

        summary = _brief_args(tool_name, arguments)
        prompt = ApprovalPrompt(
            tool_name=tool_name,
            risk_level=risk,
            summary=summary,
            arguments=arguments,
            call_id=call_id,
        )
        if self.ask_fn(prompt):
            return AuthDecision(True, f"用户已批准 user approved {tool_name}", risk_level=risk)
        return AuthDecision(False, f"用户已拒绝 user denied {tool_name}", risk_level=risk)

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
