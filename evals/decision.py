"""Decision-dimension offline scoring (Dec-C / M-D8).

Synthesizes LoopGuard / RetryPolicy trajectories — no LLM required.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.agent.loop_guard import LoopGuard, tool_call_fingerprint
from src.agent.retry_policy import RetryPolicy


@dataclass
class DecisionReport:
    """One offline decision fixture row."""

    case_id: str
    success: bool
    cycle_events: int = 0
    blocked_replays: int = 0
    stagnation_warns: int = 0
    pathology_early_stop: bool = False
    steps_equiv: int | None = None
    max_steps_budget: int | None = None
    stopped_reason: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_cycle_stop(*, max_steps_budget: int = 30) -> DecisionReport:
    """A↔B ping-pong reaches CYCLE_STOP before max_steps."""
    g = LoopGuard(cycle_warn_repeats=2, cycle_stop_repeats=3)
    cycle_warns = 0
    cycle_stops = 0
    steps = 0
    stopped = ""
    for i in range(max_steps_budget):
        steps = i + 1
        g.observe(
            "read_file" if i % 2 == 0 else "run_tests",
            {"path": "a.py"} if i % 2 == 0 else {"target": "t.py"},
        )
        hit = g.cycle_status()
        if hit is None:
            continue
        if hit.level == "warn":
            cycle_warns += 1
        if hit.level == "stop":
            cycle_stops += 1
            stopped = "cycle_detected"
            break
    early = bool(stopped) and steps < max_steps_budget
    ok = stopped == "cycle_detected" and early and steps == 6
    return DecisionReport(
        case_id="decision:cycle-stop",
        success=ok,
        cycle_events=cycle_warns + cycle_stops,
        pathology_early_stop=early,
        steps_equiv=steps,
        max_steps_budget=max_steps_budget,
        stopped_reason=stopped or "max_steps",
        notes=[
            f"cycle_warns={cycle_warns}",
            f"cycle_stops={cycle_stops}",
            "expect stop at step 6",
        ],
    )


def _run_block() -> DecisionReport:
    """Exhausted strategy → ban → next same fp returns BLOCKED (no handler)."""
    rp = RetryPolicy(max_failures=3)
    args = {"path": "x.py", "old_string": "a", "new_string": "b"}
    for _ in range(3):
        d = rp.record_failure(
            tool_name="edit_file", args=args, result="Error: boom"
        )
    assert d.should_stop
    fp = tool_call_fingerprint("edit_file", args)
    rp.ban_fingerprint(fp)
    msg = rp.blocked_tool_message("edit_file")
    ok = (
        rp.is_blocked(fp)
        and msg.startswith("Error: BLOCKED")
        and rp.block_hits >= 1
    )
    return DecisionReport(
        case_id="decision:block",
        success=ok,
        blocked_replays=rp.block_hits,
        stopped_reason="retry_exhausted",
        notes=["3 failures → ban → BLOCKED message"],
    )


def _run_stagnation_warn_only() -> DecisionReport:
    """Identical observations → STAGNATION_WARN; default stop disabled."""
    g = LoopGuard(stagnation_warn_after=3, stagnation_stop_after=0)
    warns = 0
    for _ in range(3):
        n = g.record_observation("read_file", "same body")
        sfx = g.stagnation_suffix("read_file", n)
        if sfx and "STAGNATION_WARN" in sfx:
            warns += 1
    ok = warns >= 1 and g.stagnation_stop_after == 0
    return DecisionReport(
        case_id="decision:stagnation-warn",
        success=ok,
        stagnation_warns=warns,
        stopped_reason="",
        notes=["warn-only default (stag_stop=0)"],
    )


def _run_no_false_cycle() -> DecisionReport:
    """Exact identical streak must not count as alternating cycle."""
    g = LoopGuard(cycle_warn_repeats=2, cycle_stop_repeats=3)
    for _ in range(6):
        g.observe("read_file", {"path": "same.py"})
    ok = g.cycle_status() is None and g.streak == 6
    return DecisionReport(
        case_id="decision:no-false-cycle",
        success=ok,
        cycle_events=0,
        steps_equiv=6,
        stopped_reason="",
        notes=["AAAA is exact streak, not cycle"],
    )


def _run_e3_transient_no_ban() -> DecisionReport:
    """E3: transient errors auto-retry and never enter failure_key ban."""
    from src.agent.retry_policy import RetryPolicy, call_with_transient_retry, classify_failure

    assert classify_failure(result="Error: 429 rate limit") == "transient"
    box = {"n": 0}

    def flaky() -> str:
        box["n"] += 1
        if box["n"] < 2:
            return "Error: connection reset by peer"
        return "recovered"

    out, report = call_with_transient_retry(
        flaky, max_extra=2, backoff_sec=0, sleep_fn=lambda _s: None
    )
    rp = RetryPolicy(max_failures=3)
    skipped = rp.record_failure(
        tool_name="run_shell",
        args={"command": "x"},
        result="Error: 429 rate limit",
        kind="transient",
    )
    ok = (
        out == "recovered"
        and report.recovered
        and skipped is None
        and not rp.blocked_fingerprints
        and not rp.by_key
    )
    return DecisionReport(
        case_id="decision:e3-transient-no-ban",
        success=ok,
        blocked_replays=0,
        notes=[f"attempts={report.attempts}", "transient not banned"],
    )


def _run_e3_strategy_block() -> DecisionReport:
    """E3: semantic/strategy failures ban fingerprint — no same-fp auto-replay."""
    rp = RetryPolicy(max_failures=3)
    args = {"path": "x.py", "old_string": "a", "new_string": "b"}
    for _ in range(3):
        d = rp.record_failure(
            tool_name="edit_file", args=args, result="Error: still broken"
        )
        assert d is not None
    fp = tool_call_fingerprint("edit_file", args)
    rp.ban_fingerprint(fp)
    msg = rp.blocked_tool_message("edit_file")
    ok = rp.is_blocked(fp) and msg.startswith("Error: BLOCKED") and rp.block_hits >= 1
    return DecisionReport(
        case_id="decision:e3-strategy-block",
        success=ok,
        blocked_replays=rp.block_hits,
        stopped_reason="retry_exhausted",
        notes=["semantic failures → ban → BLOCK; no strategy auto-replay"],
    )


def score_decision_offline() -> list[DecisionReport]:
    """All Dec-C offline fixtures (assertable by smoke / CLI)."""
    return [
        _run_cycle_stop(),
        _run_block(),
        _run_stagnation_warn_only(),
        _run_no_false_cycle(),
        _run_e3_transient_no_ban(),
        _run_e3_strategy_block(),
    ]


def count_pathology_from_stopped(stopped_reason: str) -> dict[str, int]:
    """Map a live AgentResult.stopped_reason into decision counters."""
    reason = (stopped_reason or "").strip()
    return {
        "cycle_events": 1 if reason == "cycle_detected" else 0,
        "blocked_replays": 1 if reason == "retry_exhausted" else 0,
        "stagnation_stops": 1 if reason == "stagnation_detected" else 0,
        "pathology": 1
        if reason
        in {"cycle_detected", "retry_exhausted", "stagnation_detected", "loop_detected"}
        else 0,
    }
