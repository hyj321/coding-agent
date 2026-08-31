"""X2: structured episode memory (JSONL) for cross-run recall.

Each finished run appends one compact record to ``.agent/episodes.jsonl``.
On a new run, recent episodes (optionally keyword-filtered by the new goal)
are injected into Current State — more reliable than free-text MEMORY alone.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agent.task_state import is_test_path

EPISODES_NAME = "episodes.jsonl"
_TOKEN = re.compile(r"[a-zA-Z_][\w\-]{2,40}|[\u4e00-\u9fff]{2,8}")
_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "test",
        "tests",
        "fix",
        "please",
        "make",
        "sure",
        "pass",
        "file",
        "files",
        "code",
        "task",
        "run",
        "true",
        "false",
    }
)


def episodes_enabled() -> bool:
    raw = (os.getenv("EPISODE_MEMORY") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def episodes_path(workdir: Path) -> Path:
    return workdir.resolve() / ".agent" / EPISODES_NAME


def build_episode(
    *,
    task: str,
    final_text: str,
    stopped_reason: str,
    memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact structured snapshot for one agent run."""
    mem = memory if isinstance(memory, dict) else {}
    ts = mem.get("task_state") if isinstance(mem.get("task_state"), dict) else {}
    test = ts.get("test_status") if isinstance(ts.get("test_status"), dict) else None
    mutated = [str(x) for x in (ts.get("mutated_paths") or []) if str(x).strip()][:12]
    focus = [str(x) for x in (mem.get("focus_files") or []) if str(x).strip()][:8]
    failed_raw = ts.get("failed") if isinstance(ts.get("failed"), list) else []
    failed_keys: list[str] = []
    for item in failed_raw[:8]:
        if isinstance(item, dict) and item.get("key"):
            failed_keys.append(str(item["key"])[:120])
        elif item:
            failed_keys.append(str(item)[:120])

    conclusion = " ".join((final_text or "").strip().split())
    if len(conclusion) > 240:
        conclusion = conclusion[:237] + "…"
    task_line = " ".join((task or "").strip().split())
    if len(task_line) > 200:
        task_line = task_line[:197] + "…"

    test_summary = ""
    test_passed: bool | None = None
    if isinstance(test, dict):
        test_passed = test.get("passed") if isinstance(test.get("passed"), bool) else None
        test_summary = str(test.get("summary") or "")[:120]

    return {
        "ts": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "task": task_line,
        "stopped_reason": str(stopped_reason or ""),
        "focus_files": focus,
        "mutated_paths": mutated,
        "source_mutated": [p for p in mutated if not is_test_path(p)][:8],
        "test_passed": test_passed,
        "test_summary": test_summary,
        "failed_keys": failed_keys,
        "conclusion": conclusion,
    }


def append_episode(workdir: Path, episode: dict[str, Any]) -> Path | None:
    """Append one JSON line. Skips empty / interrupted-only noise handled by caller."""
    if not episodes_enabled():
        return None
    if not isinstance(episode, dict) or not episode.get("task"):
        return None
    path = episodes_path(workdir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(episode, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        return None
    return path


def load_episodes(workdir: Path, *, max_lines: int = 80) -> list[dict[str, Any]]:
    path = episodes_path(workdir)
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines()[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _goal_tokens(goal: str) -> set[str]:
    out: set[str] = set()
    for tok in _TOKEN.findall(goal or ""):
        low = tok.lower()
        if low in _STOP or len(low) < 2:
            continue
        out.add(low)
    return out


def _episode_score(ep: dict[str, Any], tokens: set[str]) -> int:
    if not tokens:
        return 0
    blob = " ".join(
        [
            str(ep.get("task") or ""),
            " ".join(str(x) for x in (ep.get("focus_files") or [])[:8]),
            " ".join(str(x) for x in (ep.get("mutated_paths") or [])[:8]),
            str(ep.get("conclusion") or ""),
        ]
    ).lower()
    return sum(1 for t in tokens if t in blob)


def select_episodes_for_goal(
    episodes: list[dict[str, Any]],
    goal: str,
    *,
    max_n: int = 3,
) -> list[dict[str, Any]]:
    """Prefer goal-overlapping episodes; fall back to newest."""
    if not episodes or max_n <= 0:
        return []
    tokens = _goal_tokens(goal)
    if not tokens:
        return list(reversed(episodes[-max_n:]))
    ranked = sorted(
        enumerate(episodes),
        key=lambda iv: (_episode_score(iv[1], tokens), iv[0]),
        reverse=True,
    )
    picked = [ep for _, ep in ranked if _episode_score(ep, tokens) > 0][:max_n]
    if len(picked) < max_n:
        # pad with newest not already picked
        seen = {id(p) for p in picked}
        for ep in reversed(episodes):
            if id(ep) in seen:
                continue
            picked.append(ep)
            if len(picked) >= max_n:
                break
    return picked[:max_n]


def format_episodes_section(episodes: list[dict[str, Any]]) -> str:
    if not episodes:
        return ""
    lines = ["## Recent Episodes (structured prior runs)"]
    for i, ep in enumerate(episodes, 1):
        task = str(ep.get("task") or "（无）")
        if len(task) > 100:
            task = task[:97] + "…"
        reason = str(ep.get("stopped_reason") or "?")
        bits = [f"{i}. [{reason}] {task}"]
        files = list(ep.get("source_mutated") or ep.get("mutated_paths") or [])[:4]
        if files:
            bits.append("   files: " + ", ".join(str(f) for f in files))
        tp = ep.get("test_passed")
        if tp is True:
            bits.append(f"   tests: PASS ({ep.get('test_summary') or 'ok'})")
        elif tp is False:
            bits.append(f"   tests: FAIL ({ep.get('test_summary') or 'failed'})")
        failed = list(ep.get("failed_keys") or [])[:3]
        if failed:
            bits.append("   failed_keys: " + "; ".join(str(k) for k in failed))
        conc = str(ep.get("conclusion") or "").strip()
        if conc:
            if len(conc) > 120:
                conc = conc[:117] + "…"
            bits.append(f"   note: {conc}")
        lines.extend(bits)
    return "\n".join(lines)


def recall_episodes_for_prompt(
    workdir: Path,
    goal: str,
    *,
    max_n: int | None = None,
) -> str:
    if not episodes_enabled():
        return ""
    n = max_n
    if n is None:
        try:
            n = int(os.getenv("EPISODE_RECALL_MAX", "3") or "3")
        except ValueError:
            n = 3
    n = max(0, min(n, 8))
    if n == 0:
        return ""
    return format_episodes_section(
        select_episodes_for_goal(load_episodes(workdir), goal, max_n=n)
    )
