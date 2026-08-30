"""Completion Evidence Gate — refuse \"I'm done\" without harness evidence.

When COMPLETION_MODE=evidence and the run mutated code under a test-oriented
stop condition, a bare final answer (no tool_calls) is rejected until
``test_status.passed`` is True (or nudge budget is exhausted).
"""

from __future__ import annotations

import re
from typing import Any

from src.agent.task_state import TaskState

EVIDENCE_NUDGE_MARKER = "[completion_gate]"
_CODING_GOAL_RE = re.compile(
    r"修复|改|bug|测试|pytest|unittest|fix|fail|error|实现|编写",
    re.I,
)


def requires_evidence(
    task_state: TaskState,
    *,
    completion_mode: str = "evidence",
) -> bool:
    """True when harness should demand test evidence before accepting completed."""
    if (completion_mode or "").strip().lower() in {"trust_model", "off", "none", "false", "0"}:
        return False
    if task_state.stop_condition not in {"tests_all_pass", "tests_or_todo"}:
        return False
    if getattr(task_state, "files_mutated", False):
        return True
    goal = task_state.goal or ""
    return bool(_CODING_GOAL_RE.search(goal))


def evidence_satisfied(task_state: TaskState) -> bool:
    ts = task_state.test_status
    return bool(ts is not None and ts.passed is True)


def should_block_completion(
    task_state: TaskState,
    *,
    completion_mode: str = "evidence",
    max_nudges: int = 2,
) -> tuple[bool, str]:
    """Return (block, reason). block=False means allow Terminate."""
    if not requires_evidence(task_state, completion_mode=completion_mode):
        return False, "evidence not required"
    if evidence_satisfied(task_state):
        return False, "tests passed"
    nudges = int(getattr(task_state, "evidence_nudge_count", 0) or 0)
    if nudges >= max_nudges:
        return False, f"evidence nudge budget exhausted ({nudges}/{max_nudges})"
    return True, "missing test evidence"


def build_evidence_nudge_message(task_state: TaskState) -> str:
    summary = ""
    if task_state.test_status:
        summary = f"\n最近测试状态：{task_state.test_status.summary or '（无）'}"
    files = ", ".join(task_state.relevant_files[:5]) if task_state.relevant_files else "（未知）"
    return (
        f"{EVIDENCE_NUDGE_MARKER} 完成判定被拒绝：尚无可验证证据。"
        f"你宣称任务已完成，但测试尚未通过。"
        f"请用 run_shell 运行相关测试（如 pytest / python *_test.py），"
        f"通过后再用简体中文给出 FINAL 答复。\n"
        f"相关文件：{files}。{summary}"
    )


def note_completion_nudge(task_state: TaskState) -> int:
    task_state.evidence_nudge_count = int(getattr(task_state, "evidence_nudge_count", 0) or 0) + 1
    return task_state.evidence_nudge_count
