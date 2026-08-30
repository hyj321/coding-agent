"""Context eval runner (X1 compaction retain).

Offline (no API): observation / state / fold / microcompact retain fixtures.

Usage:
  python -m scripts.run_context_eval --offline
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from evals.context import ContextReport, score_context_offline


def _print_table(rows: list[ContextReport]) -> None:
    headers = ["case", "ok", "retained", "missing", "fold", "micro", "compress"]
    print("\n=== Context Eval (offline / X1) ===")
    print(" | ".join(headers))
    print("-" * 100)
    for r in rows:
        print(
            " | ".join(
                [
                    r.case_id,
                    "Y" if r.success else "N",
                    ",".join(r.retained[:4]) or "-",
                    ",".join(r.missing[:3]) or "-",
                    str(r.fold_events),
                    str(r.microcompact_events),
                    str(r.compress_events),
                ]
            )
        )
    print()


def run_offline() -> list[ContextReport]:
    rows = score_context_offline()
    for r in rows:
        if not r.success:
            raise AssertionError(f"context fixture failed: {r.case_id} notes={r.notes}")
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Context eval (X1)")
    p.add_argument(
        "--offline",
        action="store_true",
        required=True,
        help="No API: compaction retain fixtures",
    )
    p.add_argument(
        "--json-out",
        default=None,
        help="Optional path for JSON report (default: evals/results/context_*.json)",
    )
    args = p.parse_args(argv)

    rows = run_offline()
    _print_table(rows)

    out = args.json_out
    if out is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = Path("evals/results") / f"context_{stamp}.json"
    else:
        out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": "offline",
        "dim": "context",
        "rows": [r.to_dict() for r in rows],
        "all_ok": all(r.success for r in rows),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[context] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
