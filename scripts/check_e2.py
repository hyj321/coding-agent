"""Offline E2 check: exit 0 ≠ semantic success.

Run:
  python -m scripts.check_e2
  python -m scripts.smoke_v1
"""

from __future__ import annotations

from src.agent.completion_gate import (
    build_evidence_nudge_message,
    evidence_gaps,
    should_block_completion,
    tests_passed,
)
from src.agent.task_state import (
    TaskState,
    TestStatus,
    extract_test_targets,
    looks_like_test_command,
    parse_test_status,
    semantic_tests_passed,
    test_run_covers_task,
)


def _base(**kwargs) -> TaskState:
    return TaskState(
        goal="修复 greeter 测试失败",
        stop_condition="tests_all_pass",
        **kwargs,
    )


def main() -> None:
    print("=== E2: tighten command lookalike ===")
    assert looks_like_test_command("pytest greeter_test.py")
    assert looks_like_test_command("run_tests greeter_test.py")
    assert looks_like_test_command("python greeter_test.py")
    assert looks_like_test_command("python -m pytest tests/test_x.py -q")
    assert not looks_like_test_command("echo test")
    assert not looks_like_test_command('python -c "print(1)"')
    assert parse_test_status("echo test", "exit_code: 0\n") is None
    assert parse_test_status('python -c "print(\'test\')"', "exit_code: 0\n") is None
    print("ok non-test exit0 ignored")

    print("=== E2: unified parse + targets ===")
    structured = (
        "# run_tests python greeter_test.py\n"
        "passed: true\n"
        "exit_code: 0\n"
        "stdout:\n(empty)\n"
    )
    parsed = parse_test_status("run_tests greeter_test.py", structured)
    assert parsed is not None and parsed.passed is True
    assert parsed.exit_code == 0
    assert "greeter_test.py" in parsed.targets
    assert extract_test_targets("pytest path/to/other_test.py -q") == ["path/to/other_test.py"]
    print("ok parse targets")

    print("=== E2: unrelated test exit0 does not cover ===")
    ts = _base()
    ts.note_mutation("greeter.py")
    ts.test_status = TestStatus(
        passed=True,
        summary="exit=0",
        last_command="run_tests other_test.py",
        targets=["other_test.py"],
        exit_code=0,
    )
    assert ts.test_status.passed is True
    assert not test_run_covers_task(ts)
    assert not semantic_tests_passed(ts)
    assert not tests_passed(ts)
    assert evidence_gaps(ts) == ["irrelevant_test_run"]
    block, why = should_block_completion(ts, completion_mode="evidence", max_nudges=2)
    assert block and "irrelevant" in why, (block, why)
    msg = build_evidence_nudge_message(ts, reason=why)
    assert "E2" in msg and "[completion_gate]" in msg
    print("ok irrelevant block")

    print("=== E2: related target covers ===")
    ts2 = _base()
    ts2.note_mutation("greeter.py")
    ts2.test_status = TestStatus(
        passed=True,
        summary="1 passed",
        last_command="run_tests greeter_test.py",
        targets=["greeter_test.py"],
        exit_code=0,
    )
    assert test_run_covers_task(ts2)
    assert semantic_tests_passed(ts2)
    assert should_block_completion(ts2, completion_mode="evidence")[0] is False
    print("ok related allow")

    print("=== E2: broad suite soft-covers ===")
    ts3 = _base()
    ts3.note_mutation("greeter.py")
    ts3.test_status = TestStatus(
        passed=True,
        summary="exit=0",
        last_command="run_tests .",
        targets=["."],
        exit_code=0,
    )
    assert test_run_covers_task(ts3)
    print("ok broad cover")

    print("=== E2: update_from_tool run_tests path ===")
    ts4 = _base()
    ts4.note_mutation("greeter.py")
    ts4.update_from_tool(
        tool_name="run_tests",
        args={"target": "other_test.py", "runner": "python"},
        result=structured.replace("greeter_test.py", "other_test.py"),
    )
    assert ts4.test_status is not None and ts4.test_status.passed is True
    assert not semantic_tests_passed(ts4)
    block4, why4 = should_block_completion(ts4, completion_mode="evidence", max_nudges=2)
    assert block4 and "irrelevant" in why4, (block4, why4)
    print("ok tool path")

    print("OK")


if __name__ == "__main__":
    main()
