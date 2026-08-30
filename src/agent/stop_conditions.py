"""Stop-condition helpers: urge FINAL when goal is met (tests / todos).

Policy (UX):
- New user turn always clears prior nudge state (so "继续改" works).
- todo_all_done → soft nudge only (never force-stop).
- tests_all_pass → soft nudge; optional force-stop if agent keeps mutating.
"""

from __future__ import annotations

import re
from typing import Any

from src.agent.task_state import TaskState

FINAL_NUDGE_MARKER = "[stop_condition]"
_MUTATING_TOOLS = frozenset({"write_file", "edit_file", "run_shell"})
# Only these reasons may escalate to goal_met_forced
_FORCE_ELIGIBLE_REASONS = frozenset({"tests_all_pass"})


def todos_all_completed(todos_text: str) -> bool:
    """True when there is at least one todo and none are pending/in_progress."""
    if not todos_text or not todos_text.strip():
        return False
    statuses: list[str] = []
    for line in todos_text.splitlines():
        m = re.match(r"\s*\[([ x>\-])\]\s*\(([^)]+)\)\s*(.+)$", line)
        if not m:
            continue
        mark = m.group(1)
        status = {
            " ": "pending",
            "x": "completed",
            ">": "in_progress",
            "-": "cancelled",
        }.get(mark, "pending")
        statuses.append(status)
    if not statuses:
        return False
    open_items = [s for s in statuses if s in {"pending", "in_progress"}]
    completed = [s for s in statuses if s == "completed"]
    return not open_items and bool(completed)


def evaluate_final_nudge(
    *,
    task_state: TaskState,
    todos_text: str,
    step_had_failure: bool,
) -> tuple[bool, list[str]]:
    """Return (should_nudge, reasons)."""
    reasons: list[str] = []
    ts = task_state.test_status
    if (
        task_state.stop_condition in {"tests_all_pass", "tests_or_todo"}
        and ts is not None
        and ts.passed is True
    ):
        reasons.append("tests_all_pass")
    if todos_all_completed(todos_text) and not step_had_failure:
        # Soft signal only — UI / polish tasks often mark todos done early
        reasons.append("todo_all_done")
    return bool(reasons), reasons


def reasons_allow_force_stop(reasons: list[str]) -> bool:
    """Force-stop only when a verifiable goal (tests) was met — not mere todos."""
    return any(r in _FORCE_ELIGIBLE_REASONS for r in reasons)


def build_final_nudge_message(reasons: list[str], *, task_state: TaskState) -> str:
    bits = ", ".join(reasons) if reasons else "goal"
    extra = ""
    if task_state.test_status and task_state.test_status.passed:
        extra = f"\n测试摘要：{task_state.test_status.summary}"

    if reasons_allow_force_stop(reasons):
        return (
            f"{FINAL_NUDGE_MARKER} 可验证目标已达成（{bits}）。"
            f"请用简体中文给出 FINAL 总结（改了什么 / 测试结果），并停止继续改文件，"
            f"除非用户明确要求继续修改。{extra}"
        )
    # todo_all_done only — do not scare the model into freezing mid-polish
    return (
        f"{FINAL_NUDGE_MARKER} Todo 看起来已全部完成（{bits}）。"
        f"若用户需求已满足，请用简体中文给出简短 FINAL 总结；"
        f"若仍需小幅打磨，可以继续编辑，或为剩余工作重新打开/新增 todo。{extra}"
    )


def is_mutating_tool(name: str) -> bool:
    return name in _MUTATING_TOOLS


def post_nudge_mutating_suffix(*, count: int, limit: int) -> str:
    return (
        f"\n\n{FINAL_NUDGE_MARKER} 注意：测试已通过，但你又进行了修改"
        f"（{count}/{limit}）。若用户没有要求继续改，请停止工具调用，"
        f"用简体中文给出 FINAL 答复。"
    )


def should_force_stop_after_nudge(
    *,
    mutating_count: int,
    limit: int,
    reasons: list[str] | None,
) -> bool:
    if not reasons_allow_force_stop(reasons or []):
        return False
    return mutating_count >= limit


def force_stop_message(reasons: list[str]) -> str:
    """User-facing FINAL when harness stops after tests already passed."""
    _ = reasons  # internal codes stay out of the user text
    return (
        "相关测试已经通过，本轮已自动结束，避免继续无意义改动。"
        "若还要继续修改，直接再发一条消息即可。"
    )


def clear_nudge_state(task_state: TaskState) -> None:
    """Call at the start of each user turn so follow-ups can keep editing."""
    task_state.final_nudge_sent = False
    task_state.stop_nudge_reasons = []
    task_state.evidence_nudge_count = 0

def stop_reasons_from_memory(memory: dict[str, Any] | None) -> list[str]:
    if not isinstance(memory, dict):
        return []
    raw = memory.get("stop_nudge_reasons")
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []
