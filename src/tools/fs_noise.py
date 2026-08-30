"""Names that are agent scratch / cache — hide from explorers and prefer not listing."""

from __future__ import annotations

import re

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".agent",
        "_chunks",
    }
)

_SCRATCH_NAME_RE = re.compile(
    r"^(_s\d+\.txt|_part\d+\.txt|_split\d*\.py|_timer_full\.txt|_num\.txt)$",
    re.IGNORECASE,
)


def is_noise_entry(name: str) -> bool:
    """True for VCS/cache/agent-scratch names that should not clutter explorers."""
    if name in SKIP_DIR_NAMES:
        return True
    if name.startswith(".") and name not in {".env.example"}:
        return True
    if _SCRATCH_NAME_RE.match(name):
        return True
    return False


def is_agent_scratch(name: str) -> bool:
    """Scratch artifacts safe to delete on demo reset."""
    return name == "_chunks" or bool(_SCRATCH_NAME_RE.match(name))
