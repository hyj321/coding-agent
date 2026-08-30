"""Score a tool-call trace for Capability metrics (Cap-C / M7)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ToolEvent:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalScore:
    task_id: str
    steps: int
    used_grep: bool
    used_glob: bool
    used_run_tests: bool
    search_first: bool
    """True if grep/glob appears before a blind list_dir of '.' / empty."""
    blind_full_list_first: bool
    tool_names: list[str]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw or not isinstance(raw, str):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def extract_tool_trace(messages: list[dict[str, Any]]) -> list[ToolEvent]:
    """Extract ordered tool calls from agent messages."""
    events: list[ToolEvent] = []
    for m in messages or []:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
            name = str(fn.get("name") or "")
            if not name:
                continue
            events.append(ToolEvent(name=name, args=_parse_args(fn.get("arguments"))))
    return events


def _is_blind_list_dir(ev: ToolEvent) -> bool:
    if ev.name != "list_dir":
        return False
    path = str(ev.args.get("path") or ".").strip() or "."
    return path in {".", "", "./"}


def score_trace(
    *,
    task_id: str,
    steps: int,
    messages: list[dict[str, Any]] | None = None,
    events: list[ToolEvent] | None = None,
) -> EvalScore:
    trace = events if events is not None else extract_tool_trace(messages or [])
    names = [e.name for e in trace]
    used_grep = "grep" in names
    used_glob = "glob" in names
    used_run_tests = "run_tests" in names

    search_first = False
    blind_full_list_first = False
    for ev in trace:
        if ev.name in {"grep", "glob"}:
            search_first = True
            break
        if _is_blind_list_dir(ev):
            blind_full_list_first = True
            break
        # todo_write / load_skill before search is OK — keep scanning
        if ev.name in {"todo_write", "load_skill", "memory_search", "rag_search"}:
            continue
        if ev.name in {"read_file", "run_shell", "run_tests", "write_file", "edit_file"}:
            # Started acting without search
            break

    notes: list[str] = []
    if used_grep:
        notes.append("grep used")
    if used_run_tests:
        notes.append("run_tests used")
    if search_first:
        notes.append("search-first OK")
    elif blind_full_list_first:
        notes.append("blind list_dir before search")
    elif names:
        notes.append("no search-first signal")

    return EvalScore(
        task_id=task_id,
        steps=int(steps),
        used_grep=used_grep,
        used_glob=used_glob,
        used_run_tests=used_run_tests,
        search_first=search_first,
        blind_full_list_first=blind_full_list_first,
        tool_names=names,
        notes=notes,
    )


_PATH_HINT_RE = re.compile(r"greeter\.py", re.I)


def final_mentions_path(final_text: str, hint: str) -> bool:
    if not hint:
        return True
    text = final_text or ""
    if hint.lower() in text.lower():
        return True
    if hint.endswith(".py") and _PATH_HINT_RE.search(text):
        return True
    return False
