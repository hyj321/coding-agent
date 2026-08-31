"""Offline E3 check: transient auto-retry vs strategy BLOCK (no strategy replay).

Run:
  python -m scripts.check_e3
  python -m scripts.smoke_v1
"""

from __future__ import annotations

from src.agent.loop_guard import tool_call_fingerprint
from src.agent.retry_policy import (
    RetryPolicy,
    call_with_transient_retry,
    classify_failure,
    is_transient_exception,
    is_transient_text,
)


def main() -> None:
    print("=== E3: classify taxonomy ===")
    assert classify_failure(result="Error: boom") == "semantic"
    assert classify_failure(result="Error: rate limit 429 try again") == "transient"
    assert classify_failure(result="Error: WinError 32 file is locked") == "transient"
    assert classify_failure(result="Error: invalid json in arguments") == "format"
    assert classify_failure(result="错误：权限门拒绝了该工具 — deny") == "format"
    assert classify_failure(result="2 passed\nexit_code: 0\n") is None

    class _Rate(Exception):
        pass

    _Rate.__name__ = "RateLimitError"
    assert is_transient_exception(_Rate("slow down"))
    assert is_transient_text("Error running tool 'x': TimeoutError: timed out")
    print("ok classify")

    print("=== E3: transient auto-retry recovers (no strategy ban) ===")
    box = {"n": 0}

    def flaky() -> str:
        box["n"] += 1
        if box["n"] < 3:
            return "Error: 503 service unavailable please retry"
        return "ok after transient"

    out, report = call_with_transient_retry(
        flaky, max_extra=3, backoff_sec=0, sleep_fn=lambda _s: None
    )
    assert out == "ok after transient"
    assert report.attempts == 3 and report.recovered
    rp = RetryPolicy(max_failures=3)
    assert rp.record_failure(
        tool_name="run_shell",
        args={"command": "x"},
        result="Error: 503 service unavailable",
        kind="transient",
    ) is None
    assert not rp.by_key
    print("ok transient recover")

    print("=== E3: semantic failure bans fingerprint; no auto-replay ===")
    rp2 = RetryPolicy(max_failures=3)
    args = {"path": "x.py", "old_string": "a", "new_string": "b"}
    for i in range(3):
        d = rp2.record_failure(
            tool_name="edit_file", args=args, result="Error: AssertionError: still wrong"
        )
        assert d is not None
        assert d.kind == "semantic"
        if i < 2:
            assert not d.should_stop
        else:
            assert d.should_stop
    fp = tool_call_fingerprint("edit_file", args)
    rp2.ban_fingerprint(fp)
    assert rp2.is_blocked(fp)
    msg = rp2.blocked_tool_message("edit_file")
    assert msg.startswith("Error: BLOCKED")
    # Same fingerprint must not be "auto-run" — caller checks is_blocked first
    assert rp2.is_blocked(fp)
    print("ok strategy block")

    print("=== E3: format does not ban ===")
    rp3 = RetryPolicy(max_failures=3)
    assert (
        rp3.record_failure(
            tool_name="edit_file",
            args=args,
            result="Error: invalid json in tool arguments",
            kind="format",
        )
        is None
    )
    assert not rp3.blocked_fingerprints
    print("ok format skip")

    print("OK")


if __name__ == "__main__":
    main()
