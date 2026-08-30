"""Offline Dec-A/B/C sanity check (no API).

Run:
  python -m scripts.check_dec_a
  python -m scripts.run_decision_eval --offline
"""

from __future__ import annotations

from scripts.run_decision_eval import run_offline as run_decision_offline
from src.agent.loop_guard import LoopGuard, observation_fingerprint, tool_call_fingerprint
from src.agent.retry_policy import RetryPolicy


def main() -> None:
    print("=== cycle A<->B ===")
    g = LoopGuard(cycle_warn_repeats=2, cycle_stop_repeats=3)
    for i in range(6):
        g.observe(
            "read_file" if i % 2 == 0 else "run_tests",
            {"path": "a.py"} if i % 2 == 0 else {"target": "t.py"},
        )
        h = g.cycle_status()
        print(i + 1, None if not h else (h.level, h.repeats))

    print("=== BLOCK after 3 failures ===")
    rp = RetryPolicy(max_failures=3)
    args = {"path": "x.py", "old_string": "a", "new_string": "b"}
    for _ in range(3):
        d = rp.record_failure(
            tool_name="edit_file", args=args, result="Error: boom"
        )
        print("fail", d.count, "ban" if d.should_stop else "ok")
    fp = tool_call_fingerprint("edit_file", args)
    rp.ban_fingerprint(fp)
    print(rp.blocked_tool_message("edit_file").split("\n")[0])

    print("=== stagnation (warn-only default) ===")
    assert observation_fingerprint("t", "a  b") == observation_fingerprint("t", "a b")
    gs = LoopGuard(stagnation_warn_after=3, stagnation_stop_after=0)
    for i in range(3):
        n = gs.record_observation("read_file", "unchanged body")
        print("obs", n, "warn" if n >= 3 else "ok")
    sfx = gs.stagnation_suffix("read_file") or ""
    line = next((ln for ln in sfx.split("\n") if "STAGNATION" in ln), sfx[:60])
    print(line[:70])

    print("=== Dec-C decision offline fixtures ===")
    rows = run_decision_offline()
    for r in rows:
        print(
            r.case_id,
            "ok" if r.success else "FAIL",
            f"early={r.pathology_early_stop}",
            f"blocked={r.blocked_replays}",
        )
    print("OK")


if __name__ == "__main__":
    main()
