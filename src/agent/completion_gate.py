"""Completion Evidence Gate — refuse \"I'm done\" without harness evidence.

When COMPLETION_MODE=evidence and the run mutated code under a test-oriented
stop condition, a bare final answer (no tool_calls) is rejected until the
Mustlist is satisfied (or nudge budget is exhausted):

* Ver-A: tests green; if write/edit happened, at least one non-test source path
* Ver-B: only-test mutations + green tests → fake_green (block/warn/off)
* E2: exit 0 on an unrelated test/command is not semantic green
"""

from __future__ import annotations

import re
from typing import Any

from src.agent.task_state import TaskState, semantic_tests_passed, test_run_covers_task

EVIDENCE_NUDGE_MARKER = "[completion_gate]"
FAKE_GREEN_MARKER = "[fake_green]"
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
    if getattr(task_state, "files_mutated", False) or task_state.mutated_paths:
        return True
    goal = task_state.goal or ""
    return bool(_CODING_GOAL_RE.search(goal))


def tests_passed(task_state: TaskState) -> bool:
    """Semantic green (E2): exit-level pass is not enough if the run missed task anchors."""
    return semantic_tests_passed(task_state)


def has_source_mutation(task_state: TaskState) -> bool:
    return bool(task_state.source_mutated_paths())


def is_fake_green(task_state: TaskState) -> bool:
    """Green tests after mutating only test files (no production source edits)."""
    return tests_passed(task_state) and task_state.only_tests_mutated()


def _normalize_fake_green_mode(fake_green_mode: str) -> str:
    mode = (fake_green_mode or "block").strip().lower()
    if mode not in {"block", "warn", "off"}:
        return "block"
    return mode


def evidence_gaps(
    task_state: TaskState,
    *,
    fake_green_mode: str = "block",
) -> list[str]:
    """Ordered Mustlist gaps. Empty → hard evidence rules satisfied."""
    gaps: list[str] = []
    mode = _normalize_fake_green_mode(fake_green_mode)

    ts = task_state.test_status
    raw_green = bool(ts is not None and ts.passed is True)
    if not raw_green:
        gaps.append("missing_test_evidence")
        return gaps

    if not test_run_covers_task(task_state):
        gaps.append("irrelevant_test_run")
        return gaps

    mutated = bool(task_state.files_mutated or task_state.mutated_paths)
    if not mutated:
        return gaps  # verify-only / coding goal: green tests suffice

    if has_source_mutation(task_state):
        return gaps  # Ver-A: source edit + green tests

    # Wrote/edited but no non-test path — fake-green or legacy bool-only mutation
    if is_fake_green(task_state):
        if mode == "block":
            gaps.append("fake_green")
        elif mode == "off":
            # V2 off, Ver-A still requires a source mutation
            gaps.append("missing_source_mutation")
        # warn: soft allow (caller emits fake_green_warn)
        return gaps

    gaps.append("missing_source_mutation")
    return gaps


def evidence_satisfied(
    task_state: TaskState,
    *,
    fake_green_mode: str = "block",
) -> bool:
    return not evidence_gaps(task_state, fake_green_mode=fake_green_mode)


def should_block_completion(
    task_state: TaskState,
    *,
    completion_mode: str = "evidence",
    max_nudges: int = 2,
    fake_green_mode: str = "block",
) -> tuple[bool, str]:
    """Return (block, reason). block=False means allow Terminate."""
    if not requires_evidence(task_state, completion_mode=completion_mode):
        return False, "evidence not required"

    gaps = evidence_gaps(task_state, fake_green_mode=fake_green_mode)
    if not gaps:
        # Soft warn path: allow complete but caller may emit fake_green warn
        if is_fake_green(task_state) and (fake_green_mode or "").strip().lower() == "warn":
            return False, "tests passed with fake_green_warn"
        return False, "evidence satisfied"

    nudges = int(getattr(task_state, "evidence_nudge_count", 0) or 0)
    if nudges >= max_nudges:
        return False, f"evidence nudge budget exhausted ({nudges}/{max_nudges})"

    primary = gaps[0]
    if primary == "fake_green":
        return True, "fake_green"
    if primary == "missing_source_mutation":
        return True, "missing source mutation"
    if primary == "irrelevant_test_run":
        return True, "irrelevant test run"
    return True, "missing test evidence"


def build_evidence_nudge_message(
    task_state: TaskState,
    *,
    reason: str = "",
) -> str:
    summary = ""
    if task_state.test_status:
        summary = f"\n最近测试状态：{task_state.test_status.summary or '（无）'}"
        cmd = task_state.test_status.last_command
        if cmd:
            summary += f"\n最近测试命令：{cmd[:120]}"
        targets = task_state.test_status.targets
        if targets:
            summary += f"\n测试目标：{', '.join(targets[:5])}"
    files = ", ".join(task_state.relevant_files[:5]) if task_state.relevant_files else "（未知）"
    src = task_state.source_mutated_paths()[:5]
    tst = task_state.test_mutated_paths()[:5]
    mutation_line = ""
    if src or tst:
        bits = []
        if src:
            bits.append("源文件=" + ", ".join(src))
        if tst:
            bits.append("测试文件=" + ", ".join(tst))
        mutation_line = "\n已记录变更：" + "；".join(bits)

    why = (reason or "").strip()
    if why == "fake_green" or (is_fake_green(task_state) and "fake" in why):
        return (
            f"{EVIDENCE_NUDGE_MARKER} {FAKE_GREEN_MARKER} 完成判定被拒绝：疑似假绿。"
            f"当前仅修改了测试文件就使套件变绿，缺少对生产/目标源文件的修复证据。"
            f"请先用 edit_file/write_file 修改对应源文件（非 *_test.py / test_*.py），"
            f"再用 run_tests（推荐，如 target=greeter_test.py）验证，"
            f"通过后再用简体中文给出 FINAL 答复。\n"
            f"相关文件：{files}。{mutation_line}{summary}"
        )
    if "irrelevant" in why:
        return (
            f"{EVIDENCE_NUDGE_MARKER} 完成判定被拒绝：测试 exit 0 但未覆盖任务目标（E2）。"
            f"请用 run_tests 跑与相关文件/目标模块对应的用例"
            f"（例如改了 greeter.py 就跑 greeter_test.py），不要用无关脚本的 exit 0 冒充验证。\n"
            f"相关文件：{files}。{mutation_line}{summary}"
        )
    if "source" in why:
        return (
            f"{EVIDENCE_NUDGE_MARKER} 完成判定被拒绝：Mustlist 缺源文件变更。"
            f"测试已通过或尚可，但本 run 未记录到非测试源文件的写入/编辑。"
            f"请修改目标源文件后再跑 run_tests，通过后给出 FINAL。\n"
            f"相关文件：{files}。{mutation_line}{summary}"
        )
    return (
        f"{EVIDENCE_NUDGE_MARKER} 完成判定被拒绝：尚无可验证证据。"
        f"你宣称任务已完成，但测试尚未通过。"
        f"请用 run_tests（推荐，如 target=greeter_test.py）或 run_shell 运行相关测试，"
        f"通过后再用简体中文给出 FINAL 答复。"
        f"若已改代码，请确认改的是源文件而非只改测试。\n"
        f"相关文件：{files}。{mutation_line}{summary}"
    )


def note_completion_nudge(task_state: TaskState) -> int:
    task_state.evidence_nudge_count = int(getattr(task_state, "evidence_nudge_count", 0) or 0) + 1
    return task_state.evidence_nudge_count


def fake_green_warn_payload(task_state: TaskState) -> dict[str, Any]:
    """SSE-friendly payload when FAKE_GREEN_MODE=warn and completion is allowed."""
    return {
        "type": "completion_gate",
        "blocked": False,
        "reason": "fake_green_warn",
        "marker": FAKE_GREEN_MARKER,
        "mutated_tests": task_state.test_mutated_paths()[:8],
        "mutated_source": task_state.source_mutated_paths()[:8],
        "text": (
            f"{FAKE_GREEN_MARKER} 警告：仅测试文件被修改且测试已绿，"
            f"可能为假绿。mutated_tests={task_state.test_mutated_paths()[:5]}"
        ),
    }
