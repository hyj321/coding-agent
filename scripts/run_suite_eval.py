"""Unified improvement suite eval (Imp-A / I1 / P1-3).

One command → table: dim, task, ok, completed, steps, violated, pathology, stopped
plus KPI summary (completion / violation / pathology rates).

Usage:
  python -m scripts.run_suite_eval --offline
  python -m scripts.run_suite_eval --live
  python -m scripts.run_suite_eval --live --task fix-greeter
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.suite import (
    SuiteRow,
    print_suite_table,
    print_summary,
    run_live_suite,
    run_offline_suite,
    summarize,
)


def _default_json_path(prefix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path("evals") / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{prefix}_{stamp}.json"


def _write_json(path: Path, rows: list[SuiteRow], summary: Any) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary.to_dict(),
        "rows": [r.to_dict() for r in rows],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[suite] wrote {path}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Unified improvement suite eval (I1)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--offline",
        action="store_true",
        help="No API: Cap+Dec+Cost+Ver+Sec fixtures → one table",
    )
    g.add_argument(
        "--live",
        action="store_true",
        help="API: Capability live tasks as suite rows (needs DEEPSEEK_API_KEY)",
    )
    p.add_argument(
        "--task",
        action="append",
        dest="tasks",
        help="Live only: capability task id (repeatable)",
    )
    p.add_argument(
        "--json-out",
        default=None,
        help="Write report JSON (default: evals/results/suite_*.json)",
    )
    p.add_argument(
        "--no-json",
        action="store_true",
        help="Do not write JSON",
    )
    args = p.parse_args(argv)

    if args.offline:
        rows = run_offline_suite()
        title = "Improvement Suite Eval (offline)"
        prefix = "suite_offline"
    else:
        rows = run_live_suite(args.tasks)
        title = "Improvement Suite Eval (live)"
        prefix = "suite_live"

    summary = summarize(rows)
    print_suite_table(rows, title=title)
    print_summary(summary)

    if not args.no_json:
        out = Path(args.json_out) if args.json_out else _default_json_path(prefix)
        _write_json(out, rows, summary)

    return 0 if summary.all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
