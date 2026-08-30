"""Decision eval runner (Dec-C).

Offline (no API): cycle / BLOCK / stagnation / no-false-cycle fixtures.

Usage:
  python -m scripts.run_decision_eval --offline
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from evals.decision import DecisionReport, score_decision_offline


def _print_table(rows: list[DecisionReport]) -> None:
    headers = [
        "case",
        "ok",
        "steps",
        "cycle",
        "blocked",
        "stag_w",
        "early",
        "stopped",
    ]
    print("\n=== Decision Eval (offline) ===")
    print(" | ".join(headers))
    print("-" * 96)
    for r in rows:
        print(
            " | ".join(
                [
                    r.case_id,
                    "Y" if r.success else "N",
                    str(r.steps_equiv if r.steps_equiv is not None else "-"),
                    str(r.cycle_events),
                    str(r.blocked_replays),
                    str(r.stagnation_warns),
                    "Y" if r.pathology_early_stop else "N",
                    r.stopped_reason or "-",
                ]
            )
        )
    print()


def run_offline() -> list[DecisionReport]:
    rows = score_decision_offline()
    for r in rows:
        if not r.success:
            raise AssertionError(f"decision fixture failed: {r.case_id} notes={r.notes}")
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Decision eval (Dec-C)")
    p.add_argument(
        "--offline",
        action="store_true",
        required=True,
        help="No API: cycle/BLOCK/stagnation fixtures",
    )
    p.add_argument(
        "--json-out",
        default=None,
        help="Optional path for JSON report (default: evals/results/decision_*.json)",
    )
    args = p.parse_args(argv)

    rows = run_offline()
    _print_table(rows)

    out = args.json_out
    if out is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = Path("evals/results") / f"decision_{stamp}.json"
    else:
        out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": "offline",
        "when": datetime.now(timezone.utc).isoformat(),
        "rows": [r.to_dict() for r in rows],
        "all_ok": all(r.success for r in rows),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    return 0 if all(r.success for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
