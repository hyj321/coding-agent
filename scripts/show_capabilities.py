"""Print T1 capability snapshot (no API key).

Run:
  python -m scripts.show_capabilities
  python -m scripts.show_capabilities --workdir demos
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.agent.capabilities import build_capability_snapshot, format_capability_report

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_workdir(workdir: str | Path | None) -> Path:
    raw = Path(workdir) if workdir else PROJECT_ROOT / "demos"
    if not raw.is_absolute():
        raw = (PROJECT_ROOT / raw).resolve()
    else:
        raw = raw.resolve()
    return raw


def main() -> None:
    p = argparse.ArgumentParser(description="Show agent tools and policy snapshot (T1).")
    p.add_argument("-w", "--workdir", default="demos", help="Workdir sandbox root")
    args = p.parse_args()
    wd = resolve_workdir(args.workdir)
    if not wd.is_dir():
        raise SystemExit(f"workdir not found: {wd}")
    snap = build_capability_snapshot(wd)
    print(format_capability_report(snap))


if __name__ == "__main__":
    main()
