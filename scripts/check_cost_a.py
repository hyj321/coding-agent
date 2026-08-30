"""Offline Cost-A/B/C sanity check (no API).

Run:
  python -m scripts.check_cost_a
  python -m scripts.run_cost_eval --offline
  python -m scripts.smoke_v1
"""

from __future__ import annotations

from scripts.run_cost_eval import run_offline as run_cost_offline


def main() -> None:
    print("=== Cost-C offline fixtures ===")
    rows = run_cost_offline()
    for r in rows:
        print(
            r.case_id,
            "ok" if r.success else "FAIL",
            f"early={r.budget_stop_early}",
            f"tok={r.tokens_total_est}",
            f"stopped={r.stopped_reason or '-'}",
        )
    print("OK")


if __name__ == "__main__":
    main()
