"""Permission / sandbox helpers.

V1: path sandbox (all file ops must stay under workdir).
Later: dangerous-command approval policies can plug in here without
changing tool implementations.
"""

from __future__ import annotations

from pathlib import Path


class SandboxError(Exception):
    """Raised when a tool action violates the sandbox / policy."""


class PermissionGate:
    """Central gate for path resolution and (future) approval checks."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir.resolve()

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

    def check_shell(self, command: str) -> None:
        """V1: allow all commands (cwd is sandboxed). Day2 can add deny/ask."""
        _ = command
        return None
