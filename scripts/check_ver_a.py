"""Offline Verification Ver-A/B sanity check (no API).

Run:
  python -m scripts.check_ver_a
  python -m scripts.smoke_v1
"""

from __future__ import annotations

from src.agent.completion_gate import (
    build_evidence_nudge_message,
    evidence_gaps,
    evidence_satisfied,
    is_fake_green,
    note_completion_nudge,
    should_block_completion,
)
from src.agent.task_state import TaskState, TestStatus, is_test_path


def _base(**kwargs) -> TaskState:
    ts = TaskState(
        goal="修复 greeter 测试失败",
        stop_condition="tests_all_pass",
        **kwargs,
    )
    return ts


def main() -> None:
    print("=== Ver path heuristics ===")
    assert is_test_path("greeter_test.py")
    assert is_test_path("tests/test_greeter.py")
    assert is_test_path("test_greeter.py")
    assert is_test_path("conftest.py")
    assert not is_test_path("greeter.py")
    assert not is_test_path("src/agent/loop.py")
    print("ok heuristics")

    print("=== Ver-A: missing tests still block ===")
    ts = _base(files_mutated=True)
    ts.note_mutation("greeter.py")
    block, why = should_block_completion(ts, completion_mode="evidence", max_nudges=2)
    assert block and "evidence" in why, (block, why)
    print("ok missing_test_evidence")

    print("=== Ver-A: source mutation + green tests allow ===")
    ts = _base()
    ts.note_mutation("greeter.py")
    ts.test_status = TestStatus(passed=True, summary="1 passed", last_command="run_tests greeter_test.py")
    assert evidence_satisfied(ts)
    assert should_block_completion(ts, completion_mode="evidence")[0] is False
    print("ok source+green")

    print("=== Ver-A: coding goal, no mutation, green tests allow ===")
    ts = _base()
    ts.test_status = TestStatus(passed=True, summary="1 passed")
    assert should_block_completion(ts, completion_mode="evidence")[0] is False
    print("ok verify-only")

    print("=== Ver-B: fake green block ===")
    ts = _base()
    ts.note_mutation("greeter_test.py")
    ts.test_status = TestStatus(passed=True, summary="1 passed")
    assert is_fake_green(ts)
    assert evidence_gaps(ts, fake_green_mode="block") == ["fake_green"]
    block, why = should_block_completion(
        ts, completion_mode="evidence", max_nudges=2, fake_green_mode="block"
    )
    assert block and why == "fake_green", (block, why)
    msg = build_evidence_nudge_message(ts, reason=why)
    assert "[fake_green]" in msg and "[completion_gate]" in msg
    print("ok fake_green block")

    print("=== Ver-B: fake green warn allows ===")
    block_w, why_w = should_block_completion(
        ts, completion_mode="evidence", max_nudges=2, fake_green_mode="warn"
    )
    assert not block_w and "fake_green_warn" in why_w, (block_w, why_w)
    print("ok fake_green warn")

    print("=== Ver-B: fake green off → missing_source_mutation ===")
    block_o, why_o = should_block_completion(
        ts, completion_mode="evidence", max_nudges=2, fake_green_mode="off"
    )
    assert block_o and "source" in why_o, (block_o, why_o)
    print("ok fake_green off → Ver-A")

    print("=== Ver-B: test+source mutation not fake green ===")
    ts2 = _base()
    ts2.note_mutation("greeter_test.py")
    ts2.note_mutation("greeter.py")
    ts2.test_status = TestStatus(passed=True, summary="1 passed")
    assert not is_fake_green(ts2)
    assert should_block_completion(ts2, fake_green_mode="block")[0] is False
    print("ok mixed mutation")

    print("=== nudge budget still releases ===")
    ts3 = _base()
    ts3.note_mutation("greeter_test.py")
    ts3.test_status = TestStatus(passed=True, summary="1 passed")
    note_completion_nudge(ts3)
    note_completion_nudge(ts3)
    block_b, why_b = should_block_completion(
        ts3, completion_mode="evidence", max_nudges=2, fake_green_mode="block"
    )
    assert not block_b and "budget" in why_b, (block_b, why_b)
    print("ok nudge budget")

    print("=== trust_model skips gate ===")
    assert should_block_completion(ts3, completion_mode="trust_model")[0] is False
    print("ok trust_model")

    print("=== E2 (via check_e2) ===")
    from scripts import check_e2 as check_e2_mod

    check_e2_mod.main()

    print("OK")


if __name__ == "__main__":
    main()
