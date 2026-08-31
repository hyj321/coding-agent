"""Least-privilege tool visibility (AgenTRIM-lite).

Infer a coarse phase from the in-progress todo / goal, then expose only the
tools that phase needs. Default is ``full`` (no narrowing) when signals are weak,
so we never strand the agent without write/shell by accident.
"""

from __future__ import annotations

import re
from src.tools.base import ToolRegistry

Phase = str  # "explore" | "edit" | "verify" | "full"

_EXPLORE_RE = re.compile(
    r"定位|搜索|查找|阅读|只读|浏览|locate|search|read|explore|inspect|investigate",
    re.I,
)
_EDIT_RE = re.compile(
    r"修复|修改|编辑|改写|实现|写入|fix|edit|write|implement|patch|change",
    re.I,
)
_VERIFY_RE = re.compile(
    r"测试|验证|跑测|回归|pytest|unittest|verify|test|check|validate",
    re.I,
)

_READONLY = frozenset(
    {
        "list_dir",
        "glob",
        "grep",
        "read_file",
        "todo_write",
        "load_skill",
        "memory_search",
        "rag_search",
        "git_status",
        "git_diff",
        "ask_user",
    }
)
_PHASE_ALLOW: dict[str, frozenset[str] | None] = {
    # Explore: no mutating filesystem / shell
    "explore": _READONLY,
    # Edit: full toolkit (still gated by PermissionGate)
    "edit": None,
    # Verify: tests + quick fix; hide write_file (prefer edit_file)
    "verify": _READONLY | frozenset({"run_shell", "run_tests", "edit_file"}),
    # Full: no filter
    "full": None,
}


def _in_progress_todo_text(todos_text: str) -> str:
    for line in (todos_text or "").splitlines():
        # TodoStore render: "  [>] (id) content"
        m = re.match(r"\s*\[>\]\s*\([^)]*\)\s*(.+)$", line)
        if m:
            return m.group(1).strip()
    return ""


def infer_phase(
    *,
    todos_text: str = "",
    goal: str = "",
    files_mutated: bool = False,
    tests_passed: bool | None = None,
) -> Phase:
    """Return explore | edit | verify | full."""
    focus = _in_progress_todo_text(todos_text) or (goal or "")
    if focus:
        if _VERIFY_RE.search(focus):
            return "verify"
        if _EDIT_RE.search(focus):
            return "edit"
        if _EXPLORE_RE.search(focus):
            return "explore"

    # Soft heuristic without clear todo wording
    if files_mutated and tests_passed is not True:
        return "verify"
    if files_mutated:
        return "edit"
    return "full"


def visible_tool_names(registry: ToolRegistry, phase: Phase) -> list[str]:
    """Names to expose this step (sorted). Unknown phase → full."""
    allow = _PHASE_ALLOW.get(phase, None)
    all_names = registry.names()
    if allow is None:
        return all_names
    return sorted(n for n in all_names if n in allow)


def filter_openai_tools(
    registry: ToolRegistry,
    phase: Phase,
) -> tuple[list[dict], list[str], Phase]:
    """Return (openai_tools_payload, visible_names, phase)."""
    names = visible_tool_names(registry, phase)
    return registry.openai_tools(names=names), names, phase
