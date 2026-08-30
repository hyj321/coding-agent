"""Project-level long-term memory via MEMORY.md (MemGPT-style editable block).

Short-term / working memory stays in ContextManager + working_memory.json;
MEMORY.md is the archival layer that persists across runs.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MEMORY_CANDIDATES = ("MEMORY.md", ".agent/MEMORY.md")
MEMORY_HEADER = """# Project Memory

Cross-run notes for the coding agent (facts, conventions, pitfalls, what changed).
Edit freely; the agent appends a short entry after each completed run.
Newest entries are at the bottom; the agent prefers the recent tail on load.

---
"""

WORKING_MEMORY_NAME = "working_memory.json"


def resolve_memory_path(workdir: Path) -> Path:
    """Prefer an existing MEMORY.md / .agent/MEMORY.md; else default to workdir/MEMORY.md."""
    root = workdir.resolve()
    for name in MEMORY_CANDIDATES:
        path = root / name
        if path.is_file():
            return path
    agent_dir = root / ".agent"
    if agent_dir.is_dir():
        return agent_dir / "MEMORY.md"
    return root / "MEMORY.md"


def working_memory_path(workdir: Path) -> Path:
    """Raschka dual-track: small working snapshot beside the workdir (not full transcript)."""
    return workdir.resolve() / ".agent" / WORKING_MEMORY_NAME


def load_memory_excerpt(workdir: Path, *, max_chars: int = 3000) -> str:
    """Load MEMORY.md for prompt injection; prefer the recent tail when long."""
    path = resolve_memory_path(workdir)
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    text = text.strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    # Prefer recent entries (tail), keep a short head marker
    tail = text[-max_chars:]
    cut = tail.find("\n## ")
    if cut > 0 and cut < len(tail) // 2:
        tail = tail[cut + 1 :]
    return "…(earlier MEMORY.md truncated)…\n" + tail.lstrip()


def format_memory_section(excerpt: str) -> str:
    if not excerpt.strip():
        return ""
    return "## Project Memory (from MEMORY.md)\n" + excerpt.strip()


def save_working_memory(
    workdir: Path,
    snapshot: dict[str, Any] | None,
    *,
    transcript_dir: Path | None = None,
) -> Path | None:
    """Persist a compact working-memory snapshot (dual-track with full transcript)."""
    if not snapshot:
        return None
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "workdir": str(workdir.resolve()),
        **snapshot,
    }
    path = working_memory_path(workdir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return None
    if transcript_dir is not None:
        try:
            transcript_dir.mkdir(parents=True, exist_ok=True)
            twin = transcript_dir / WORKING_MEMORY_NAME
            twin.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return path


def load_working_memory(workdir: Path) -> dict[str, Any] | None:
    path = working_memory_path(workdir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def format_turn_summary(
    *,
    task: str,
    final_text: str,
    stopped_reason: str,
    memory: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> str:
    """Rule-based turn summary for UI + MEMORY (no extra LLM call)."""
    mem = memory or {}
    task_line = " ".join((task or "").strip().split())
    if len(task_line) > 160:
        task_line = task_line[:157] + "…"
    conclusion = " ".join((final_text or "").strip().split())
    if len(conclusion) > 500:
        conclusion = conclusion[:497] + "…"
    files = list(mem.get("focus_files") or [])[:8]
    related = list(mem.get("related_files") or [])[:6]
    errors = list(mem.get("last_errors") or [])[:3]
    todos = str(mem.get("todos_text") or "").strip()
    history = str(mem.get("history_summary") or "").strip()

    status_zh = {
        "completed": "已完成",
        "max_steps": "达到最大步数",
        "interrupted": "已中断",
        "loop_detected": "检测到循环",
        "retry_exhausted": "重试耗尽",
        "goal_met_forced": "目标已达成（强制收尾）",
    }.get(stopped_reason, stopped_reason)

    lines = [
        "## 本轮总结",
        f"- **任务：** {task_line or '（无）'}",
        f"- **状态：** {status_zh}",
    ]
    if files:
        lines.append("- **关键文件：** " + ", ".join(files))
    if related:
        lines.append("- **相关：** " + ", ".join(related))
    if errors:
        lines.append("- **错误 / 注意：**")
        for e in errors:
            lines.append(f"  - {e}")
    if todos:
        flat = todos.replace("\n", " | ")
        if len(flat) > 280:
            flat = flat[:277] + "…"
        lines.append(f"- **待办：** {flat}")
    if history:
        hist_lines = [ln.strip() for ln in history.splitlines() if ln.strip()][:5]
        if hist_lines:
            lines.append("- **阶段备注：**")
            for ln in hist_lines:
                lines.append(f"  - {ln.lstrip('- ')}")
    if conclusion:
        lines.append(f"- **结论：** {conclusion}")
    if usage:
        lines.append(
            f"- **上下文：** 剩余 {usage.get('remaining_pct')}% "
            f"({usage.get('used_tokens')}/{usage.get('budget_tokens')} tok，"
            f"level={usage.get('level')})"
        )
    lines.append(
        "- **续写提示：** 后续任务可依赖 MEMORY.md / working_memory；"
        "细节以磁盘文件为准，必要时 read_file。"
    )
    return "\n".join(lines).strip() + "\n"


def save_turn_summary_file(workdir: Path, summary: str) -> Path | None:
    """Write latest turn summary for demos / interview replay."""
    try:
        path = workdir.resolve() / ".agent" / "last_turn_summary.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(summary, encoding="utf-8")
        return path
    except OSError:
        return None


def append_run_to_memory(
    workdir: Path,
    *,
    task: str,
    final_text: str,
    stopped_reason: str,
    memory: dict[str, Any] | None = None,
    max_entry_chars: int = 2200,
    usage: dict[str, Any] | None = None,
) -> Path | None:
    """Append a rule-template entry (no LLM). Returns path written, or None on skip/error."""
    if stopped_reason == "interrupted":
        return None

    path = resolve_memory_path(workdir)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry_body = format_turn_summary(
        task=task,
        final_text=final_text,
        stopped_reason=stopped_reason,
        memory=memory,
        usage=usage,
    )
    # MEMORY uses dated heading
    entry = entry_body.replace(
        "## Turn Summary",
        f"## [{stamp}] Turn Summary",
        1,
    )
    if len(entry) > max_entry_chars:
        entry = entry[: max_entry_chars - 20] + "\n…(entry truncated)\n"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            path.write_text(MEMORY_HEADER + "\n" + entry + "\n", encoding="utf-8")
        else:
            existing = path.read_text(encoding="utf-8")
            sep = "" if existing.endswith("\n") else "\n"
            path.write_text(existing + sep + "\n" + entry + "\n", encoding="utf-8")
    except OSError:
        return None
    return path


def search_memory_sources(
    *,
    workdir: Path,
    query: str,
    transcript_dir: Path | None = None,
    max_hits: int = 12,
    max_snippet: int = 220,
) -> str:
    """Keyword search over MEMORY.md + transcripts (no embeddings)."""
    q = (query or "").strip()
    if not q:
        return "Error: 'query' is required"
    terms = [t for t in re.split(r"\s+", q.lower()) if t]
    if not terms:
        return "Error: empty query"

    hits: list[tuple[int, str, str]] = []

    mem_path = resolve_memory_path(workdir)
    if mem_path.is_file():
        try:
            text = mem_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        for block in _split_memory_blocks(text):
            score = _keyword_score(block, terms)
            if score > 0:
                hits.append((score, f"MEMORY.md:{mem_path.name}", _clip(block, max_snippet)))

    roots: list[Path] = []
    if transcript_dir is not None and transcript_dir.is_dir():
        roots.append(transcript_dir.resolve())
    default_t = Path("transcripts").resolve()
    if default_t.is_dir() and default_t not in roots:
        roots.append(default_t)

    seen_files: set[Path] = set()
    for root in roots:
        for path in sorted(root.glob("session_*.json")) + sorted(root.glob("run_*.json")):
            if path in seen_files:
                continue
            seen_files.add(path)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for label, chunk in _transcript_search_chunks(data):
                score = _keyword_score(chunk, terms)
                if score > 0:
                    hits.append((score, f"{path.name}:{label}", _clip(chunk, max_snippet)))

    if not hits:
        return f"No matches for {q!r} in MEMORY.md / transcripts."

    hits.sort(key=lambda h: (-h[0], h[1]))
    lines = [f"memory_search results for {q!r} (top {min(max_hits, len(hits))}):"]
    for score, source, snippet in hits[:max_hits]:
        lines.append(f"- [{score}] {source}")
        lines.append(f"  {snippet.replace(chr(10), ' / ')}")
    return "\n".join(lines)


def _split_memory_blocks(text: str) -> list[str]:
    if not text.strip():
        return []
    parts = re.split(r"(?m)(?=^## )", text)
    return [p.strip() for p in parts if p.strip()]


def _transcript_search_chunks(data: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    task = str(data.get("task") or "")
    if task:
        out.append(("task", task))
    final = str(data.get("final_text") or "")
    if final:
        out.append(("final", final))
    mem = data.get("memory")
    if isinstance(mem, dict):
        for key in ("history_summary", "todos_text", "focus_files", "last_errors"):
            val = mem.get(key)
            if val:
                out.append((f"memory.{key}", str(val)))
    for i, turn in enumerate(data.get("turns") or []):
        if not isinstance(turn, dict):
            continue
        ttask = str(turn.get("task") or "")
        tfinal = str(turn.get("final_text") or "")
        if ttask:
            out.append((f"turn{i}.task", ttask))
        if tfinal:
            out.append((f"turn{i}.final", tfinal))
    return out


def _keyword_score(text: str, terms: list[str]) -> int:
    low = text.lower()
    score = 0
    for t in terms:
        if t in low:
            score += 1 + low.count(t)
    return score


def _clip(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"
