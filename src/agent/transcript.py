"""Persist agent runs for debugging, demos, and interviews."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agent.loop import AgentResult


def save_transcript(
    directory: Path,
    *,
    task: str,
    result: AgentResult,
    meta: dict[str, Any] | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"run_{stamp}.json"
    payload = {
        "created_at": stamp,
        "task": task,
        "stopped_reason": result.stopped_reason,
        "steps": result.steps,
        "final_text": result.final_text,
        "meta": meta or {},
        "messages": result.messages,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
