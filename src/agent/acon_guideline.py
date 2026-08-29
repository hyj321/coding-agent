"""ACON-lite guideline store: failure pairs → tighten observation compression.

Full ACON trains a compressor from failure pairs offline. We keep a tiny
JSON guideline under `.agent/compress_guideline.json` and bump soft/hard
limits + keep/drop hints when the agent repeatedly fails on verbose tools.
No distilled model — rule updates only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_GUIDELINE: dict[str, Any] = {
    "version": 1,
    "soft_limit": 1200,
    "hard_limit": 2400,
    "stub_limit": 180,
    "microcompact_keep_recent_tools": 4,
    "keep": [
        "exit_code",
        "FAILED/ERROR test names",
        "exception type + short message",
        "paths touched",
        "todo list",
    ],
    "drop": [
        "decorative separators",
        "repeated stack frames beyond top",
        "long successful stdout",
    ],
    "tool_limits": {},
    "failure_pairs": [],
    "updates": 0,
}


def guideline_path(workdir: Path) -> Path:
    return workdir.resolve() / ".agent" / "compress_guideline.json"


def load_guideline(workdir: Path) -> dict[str, Any]:
    path = guideline_path(workdir)
    if not path.is_file():
        return dict(DEFAULT_GUIDELINE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_GUIDELINE)
    if not isinstance(data, dict):
        return dict(DEFAULT_GUIDELINE)
    merged = dict(DEFAULT_GUIDELINE)
    merged.update(data)
    return merged


def save_guideline(workdir: Path, guideline: dict[str, Any]) -> Path | None:
    path = guideline_path(workdir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        guideline = dict(guideline)
        guideline["updated_at"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path.write_text(json.dumps(guideline, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None


def record_failure_pair(
    workdir: Path,
    *,
    tool_name: str,
    observation_preview: str,
    recovered: bool,
) -> dict[str, Any]:
    """Append a failure pair and optionally tighten limits for that tool."""
    g = load_guideline(workdir)
    pairs = list(g.get("failure_pairs") or [])
    preview = " ".join((observation_preview or "").split())[:240]
    raw_len = len(observation_preview or "")
    pairs.append(
        {
            "tool": tool_name,
            "preview": preview,
            "recovered": recovered,
            "at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        }
    )
    g["failure_pairs"] = pairs[-40:]

    # Heuristic update: verbose failures on a tool → lower soft_limit for it
    tool_limits = dict(g.get("tool_limits") or {})
    key = (tool_name or "tool").lower()
    cur = int(tool_limits.get(key) or g.get("soft_limit") or 1200)
    if not recovered and raw_len > 200:
        cur = max(400, int(cur * 0.85))
        tool_limits[key] = cur
        g["updates"] = int(g.get("updates") or 0) + 1
        drop = list(g.get("drop") or [])
        hint = f"trim long {key} stdout earlier"
        if hint not in drop:
            drop.append(hint)
            g["drop"] = drop[-20:]
    elif recovered:
        # slight relaxation so we do not collapse forever
        cur = min(2400, int(cur * 1.05))
        tool_limits[key] = cur
    g["tool_limits"] = tool_limits
    save_guideline(workdir, g)
    return g


def limits_for_tool(guideline: dict[str, Any], tool_name: str) -> tuple[int, int, int]:
    soft = int(guideline.get("soft_limit") or 1200)
    hard = int(guideline.get("hard_limit") or 2400)
    stub = int(guideline.get("stub_limit") or 180)
    tool_limits = guideline.get("tool_limits") or {}
    key = (tool_name or "").lower()
    if key in tool_limits:
        soft = int(tool_limits[key])
        hard = max(hard, soft + 400)
    return soft, hard, stub
