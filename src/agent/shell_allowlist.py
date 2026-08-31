"""Shell command prefix allowlist (S5 / M-T2).

When ``SHELL_MODE=allowlist``, ``run_shell`` only permits commands whose
normalized prefix matches the configured allowlist (after hard-deny checks).
"""

from __future__ import annotations

import os
import re
from typing import Literal, Sequence

ShellMode = Literal["open", "allowlist"]

# Default prefixes: pytest / python runners / read-only git via shell.
DEFAULT_SHELL_ALLOWLIST_PREFIXES: tuple[str, ...] = (
    "pytest",
    "python",
    "python3",
    "py",
    "python -m pytest",
    "python3 -m pytest",
    "python -m unittest",
    "python3 -m unittest",
    "git status",
    "git diff",
    "git log",
    "git show",
    "git branch",
    "git rev-parse",
    "git",
)


def parse_shell_mode(value: str | None) -> ShellMode:
    raw = (value or "open").strip().lower()
    if raw in {"open", "allowlist"}:
        return raw  # type: ignore[return-value]
    return "open"


def parse_shell_allowlist(value: str | None) -> tuple[str, ...]:
    """Parse ``SHELL_ALLOWLIST`` env (comma-separated prefixes)."""
    if not value or not str(value).strip():
        return DEFAULT_SHELL_ALLOWLIST_PREFIXES
    parts = [p.strip() for p in str(value).split(",") if p.strip()]
    return tuple(parts) if parts else DEFAULT_SHELL_ALLOWLIST_PREFIXES


def load_shell_allowlist_from_env() -> tuple[str, ...]:
    return parse_shell_allowlist(os.getenv("SHELL_ALLOWLIST"))


def normalize_shell_command(command: str) -> str:
    """Collapse whitespace for stable prefix matching."""
    return re.sub(r"\s+", " ", (command or "").strip()).lower()


def shell_matches_allowlist(command: str, prefixes: Sequence[str]) -> bool:
    """Return True if *command* starts with any allowed prefix."""
    cmd = normalize_shell_command(command)
    if not cmd:
        return False
    ordered = sorted(prefixes, key=len, reverse=True)
    for raw_prefix in ordered:
        prefix = normalize_shell_command(raw_prefix)
        if not prefix:
            continue
        if cmd == prefix or cmd.startswith(prefix + " "):
            return True
    return False


def shell_allowlist_deny_reason(command: str, prefixes: Sequence[str]) -> str:
    preview = (command or "").strip()
    if len(preview) > 80:
        preview = preview[:80] + "..."
    sample = ", ".join(prefixes[:6])
    extra = "" if len(prefixes) <= 6 else f", … (+{len(prefixes) - 6} more)"
    return (
        "已拒绝 shell 命令 denied by SHELL_MODE=allowlist "
        f"(command {preview!r} is not in the allowed prefix list; "
        f"allowed examples: {sample}{extra})"
    )
