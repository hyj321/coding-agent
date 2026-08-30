"""Offline X1 check: context compaction retains critical paths / asserts.

Run:
  python -m scripts.check_x1
  python -m scripts.run_context_eval --offline
  python -m scripts.smoke_v1
"""

from __future__ import annotations

from evals.context import score_context_offline


def main() -> None:
    print("=== X1 context compaction retain ===")
    rows = score_context_offline()
    for r in rows:
        status = "ok" if r.success else "FAIL"
        miss = f" missing={r.missing}" if r.missing else ""
        print(
            f"[{status}] {r.case_id} retained={r.retained}"
            f"{miss} fold={r.fold_events} micro={r.microcompact_events}"
        )
        if not r.success:
            raise AssertionError(f"X1 fixture failed: {r.case_id} notes={r.notes}")
    print("OK")


if __name__ == "__main__":
    main()
