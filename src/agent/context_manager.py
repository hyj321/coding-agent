"""ACON-inspired Context Manager for long-horizon coding agents.

Prompt layout (P1 — cache-friendly):
  STABLE PREFIX: system (rules + tools + frozen workspace snapshot) + original task
  VARIABLE SUFFIX: Current State / Historical / root reinject note (appended at end)

Five layers assembled into the model prompt (progressive disclosure):
  1. System Context   — tools, workdir, rules (stable within a run)
  2. Task Context     — original user goal
  3. Current State    — focus files, todos, last errors, MEMORY.md
  4. Recent Actions   — last K tool turns (full-ish, already observation-compressed)
  5. Historical Context — summarized older turns (not raw dumps)

Budget gate (chars≈tokens*4): when exceeded, fold older turns into Historical Context
while keeping goal / state / recent actions — never naive head truncation only.
After fold, re-inject MEMORY + focus_files + incomplete todos from live state/disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.agent.acon_guideline import (
    limits_for_tool,
    load_guideline,
    record_failure_pair,
)
from src.agent.compress import (
    compress_tool_result,
    estimate_messages_tokens,
    extract_paths_from_args,
    microcompact_messages,
    related_test_paths,
)
from src.agent.memory import format_memory_section, load_memory_excerpt
from src.tools.filesystem import snapshot_workdir

CONTEXT_NOTE_MARKER = "[Context Manager — layered working memory]"
ROOT_REINJECT_MARKER = "[Root State — re-injected after context fold]"


@dataclass
class ActionRecord:
    step: int
    tool: str
    path: str | None
    ok: bool
    summary: str


@dataclass
class ContextState:
    task: str = ""
    focus_files: list[str] = field(default_factory=list)
    related_files: list[str] = field(default_factory=list)
    last_errors: list[str] = field(default_factory=list)
    todos_text: str = ""
    actions: list[ActionRecord] = field(default_factory=list)
    history_summary: str = ""
    compress_events: int = 0
    project_memory: str = ""
    phase_compress_events: int = 0
    fold_events: int = 0
    microcompact_events: int = 0
    guideline_updates: int = 0
    layout_mode: str = "prefix_stable_suffix_variable"
    last_failed_tool: str | None = None
    last_failed_preview: str = ""

    def note_path(self, path: str | None) -> None:
        if not path:
            return
        p = path.replace("\\", "/")
        if p in self.focus_files:
            self.focus_files.remove(p)
        self.focus_files.insert(0, p)
        self.focus_files = self.focus_files[:8]
        for rel in related_test_paths(p):
            if rel not in self.related_files and rel not in self.focus_files:
                self.related_files.append(rel)
        self.related_files = self.related_files[:10]

    def note_error(self, text: str) -> None:
        line = " ".join(text.strip().split())
        if not line:
            return
        if len(line) > 220:
            line = line[:220] + "…"
        self.last_errors = [line] + [e for e in self.last_errors if e != line]
        self.last_errors = self.last_errors[:5]

    def render_current_state(self) -> str:
        lines = ["## Current State"]
        if self.focus_files:
            lines.append("Focus files (prefer these; re-read only if needed):")
            for f in self.focus_files:
                lines.append(f"- {f}")
        if self.related_files:
            lines.append("Likely related tests / files:")
            for f in self.related_files[:6]:
                lines.append(f"- {f}")
        if self.last_errors:
            lines.append("Recent errors / test failures:")
            for e in self.last_errors:
                lines.append(f"- {e}")
        if self.todos_text:
            lines.append("Todo:")
            lines.append(self.todos_text)
        if self.project_memory:
            mem = format_memory_section(self.project_memory)
            if mem:
                lines.append(mem)
        if len(lines) == 1:
            lines.append("(no mutable state yet)")
        return "\n".join(lines)

    def render_history(self) -> str:
        if not self.history_summary and not self.actions:
            return ""
        lines = ["## Historical Context"]
        if self.history_summary:
            lines.append(self.history_summary)
        # Always include a short action ledger (progressive: summary first)
        if self.actions:
            lines.append("Action ledger (oldest → newest):")
            for a in self.actions[-20:]:
                mark = "ok" if a.ok else "ERR"
                path = f" {a.path}" if a.path else ""
                lines.append(f"- step {a.step}: {a.tool}{path} [{mark}] — {a.summary}")
        return "\n".join(lines)


class ContextManager:
    """Owns layered assembly + observation compression + history fold."""

    def __init__(
        self,
        *,
        workdir: Path,
        tool_names: list[str],
        token_budget: int = 8000,
        recent_keep_messages: int = 16,
        observation_soft_chars: int = 1200,
        observation_hard_chars: int = 2400,
    ) -> None:
        self.workdir = workdir
        self.tool_names = tool_names
        self.token_budget = max(2000, token_budget)
        self.recent_keep_messages = max(6, recent_keep_messages)
        self.observation_soft_chars = observation_soft_chars
        self.observation_hard_chars = observation_hard_chars
        self.state = ContextState()
        self._system_prompt_cache: str | None = None
        self._guideline = load_guideline(self.workdir)
        self.reload_project_memory()

    def reload_project_memory(self, *, max_chars: int = 3000) -> None:
        """Load / refresh MEMORY.md excerpt into Current State (cross-run long memory)."""
        self.state.project_memory = load_memory_excerpt(self.workdir, max_chars=max_chars)

    def import_memory(self, snapshot: dict[str, Any] | None) -> None:
        """Hydrate working memory from a prior session / working_memory.json export."""
        if not snapshot:
            return
        self.state.task = str(snapshot.get("task") or self.state.task)
        for key in ("focus_files", "related_files", "last_errors"):
            val = snapshot.get(key)
            if isinstance(val, list):
                setattr(self.state, key, [str(x) for x in val][:12])
        if snapshot.get("history_summary"):
            self.state.history_summary = str(snapshot["history_summary"])
        if snapshot.get("todos_text"):
            self.state.todos_text = str(snapshot["todos_text"])
        actions = snapshot.get("actions")
        if isinstance(actions, list):
            restored: list[ActionRecord] = []
            for a in actions[-30:]:
                if not isinstance(a, dict):
                    continue
                restored.append(
                    ActionRecord(
                        step=int(a.get("step") or 0),
                        tool=str(a.get("tool") or "tool"),
                        path=a.get("path"),
                        ok=bool(a.get("ok", True)),
                        summary=str(a.get("summary") or "")[:160],
                    )
                )
            if restored:
                self.state.actions = restored

    def build_system_prompt(self) -> str:
        """Stable within a run — cached so prompt-prefix bytes do not churn each step."""
        if self._system_prompt_cache is not None:
            return self._system_prompt_cache
        snapshot = snapshot_workdir(self.workdir)
        tools_line = ", ".join(self.tool_names)
        self._system_prompt_cache = f"""You are a coding agent running on the user's machine.
You solve programming tasks by calling tools.

## System Context
1. Working directory: {self.workdir}
2. Paths are relative to that directory unless stated otherwise.
3. Available tools: {tools_line}
4. Prefer read_file / list_dir / glob before editing.
5. Prefer edit_file for small edits; write_file for create/rewrite.
6. Use run_shell for tests/scripts (non-interactive).
7. Plan-then-Act: for non-trivial tasks, FIRST todo_write a checklist, keep one
   item in_progress, update as you go; finish checklist before the final answer.
8. When done, give a clear final answer and stop calling tools.
9. On tool errors, adjust approach briefly and retry.
10. Project Memory (MEMORY.md) may appear under Current State — treat it as
    durable cross-run notes (conventions / past pitfalls); do not ignore it.
11. Use memory_search for keyword recall; rag_search for local TF–IDF semantic recall.
12. Prompt layout: system+task are a stable prefix; working memory is a variable suffix.

## Progressive Disclosure
- Do NOT paste entire files into your reasoning.
- Start from summaries / Current State / Historical Context below.
- Call read_file only for the slice you need; prefer focused edits.
- Relevant files are hinted in Current State — prioritize those over the whole repo.

## Workspace snapshot (top-level only; frozen at run start)
{snapshot}
"""
        return self._system_prompt_cache

    def observe_tool(
        self,
        *,
        step: int,
        tool_name: str,
        raw_args: str | dict[str, Any],
        result: str,
    ) -> str:
        """Compress observation, update state, return text to store in messages."""
        soft, hard, stub = limits_for_tool(self._guideline, tool_name)
        soft = min(soft, self.observation_soft_chars)
        hard = min(hard, self.observation_hard_chars)
        compressed = compress_tool_result(
            tool_name,
            result,
            soft_limit=soft,
            hard_limit=hard,
            stub_limit=stub,
            tier="full",
        )
        if compressed != result:
            self.state.compress_events += 1

        paths = extract_paths_from_args(raw_args)
        for p in paths:
            self.state.note_path(p)

        ok = not str(result).startswith("Error")
        failedish = (not ok) or bool(
            re.search(r"(?m)^(FAILED|ERROR)\b|exit_code:\s*[1-9]", compressed)
        )
        if failedish:
            self.state.note_error(compressed.splitlines()[0] if compressed else result[:200])
            self.state.last_failed_tool = tool_name
            self.state.last_failed_preview = compressed[:400]
            self._guideline = record_failure_pair(
                self.workdir,
                tool_name=tool_name,
                observation_preview=compressed,
                recovered=False,
            )
            self.state.guideline_updates = int(self._guideline.get("updates") or 0)
        elif self.state.last_failed_tool:
            # Recovered after a failure — record positive pair, clear latch
            self._guideline = record_failure_pair(
                self.workdir,
                tool_name=self.state.last_failed_tool,
                observation_preview=self.state.last_failed_preview,
                recovered=True,
            )
            self.state.guideline_updates = int(self._guideline.get("updates") or 0)
            self.state.last_failed_tool = None
            self.state.last_failed_preview = ""

        summary = compressed.replace("\n", " ")
        if len(summary) > 160:
            summary = summary[:157] + "…"

        if tool_name == "todo_write" and compressed.startswith("Todo list:"):
            self._phase_compress_on_todo(step=step, new_todos_text=compressed, summary=summary)
        else:
            self.state.actions.append(
                ActionRecord(
                    step=step,
                    tool=tool_name,
                    path=paths[0] if paths else None,
                    ok=ok,
                    summary=summary,
                )
            )
            if len(self.state.actions) > 60:
                old = self.state.actions[:-40]
                self.state.actions = self.state.actions[-40:]
                self.state.history_summary = self._merge_summary(
                    self.state.history_summary,
                    self._summarize_actions(old),
                )
        return compressed

    def _phase_compress_on_todo(
        self,
        *,
        step: int,
        new_todos_text: str,
        summary: str,
    ) -> None:
        """When a todo flips to completed, fold recent tool trail into history_summary."""
        prev_status = _parse_todo_status_map(self.state.todos_text)
        new_status = _parse_todo_status_map(new_todos_text)
        newly_done = [
            tid
            for tid, st in new_status.items()
            if st == "completed" and prev_status.get(tid) != "completed"
        ]
        self.state.todos_text = new_todos_text

        # Actions belonging to the phase that just finished (since previous todo_write)
        trail = list(self.state.actions)
        last_todo_idx = -1
        for i in range(len(trail) - 1, -1, -1):
            if trail[i].tool == "todo_write":
                last_todo_idx = i
                break
        phase_actions = trail[last_todo_idx + 1 :] if last_todo_idx >= 0 else trail[-12:]

        self.state.actions.append(
            ActionRecord(
                step=step,
                tool="todo_write",
                path=None,
                ok=True,
                summary=summary,
            )
        )

        if not newly_done:
            return

        if not phase_actions:
            phase_actions = trail[-8:]

        bullets = _phase_bullets(phase_actions, limit=5)
        if not bullets:
            return

        labels = ", ".join(newly_done)
        block = f"Phase done (todo {labels}):\n" + "\n".join(f"- {b}" for b in bullets)
        self.state.history_summary = self._merge_summary(self.state.history_summary, block)
        self.state.phase_compress_events += 1
        # Keep a short recent ledger; folded detail lives in history_summary
        keep_todos = [a for a in self.state.actions if a.tool == "todo_write"][-3:]
        recent_other = [a for a in self.state.actions if a.tool != "todo_write"][-4:]
        merged = recent_other + keep_todos
        merged.sort(key=lambda a: a.step)
        self.state.actions = merged

    def prepare_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        user_task: str,
    ) -> list[dict[str, Any]]:
        """Budget-aware copy: stable system prefix + variable working-memory suffix."""
        self.state.task = user_task or self.state.task
        prepared = [dict(m) for m in messages]

        # Stable prefix: cached system prompt (bytes fixed for this run)
        if prepared and prepared[0].get("role") == "system":
            prepared[0] = {
                "role": "system",
                "content": self.build_system_prompt(),
            }

        # Variable suffix: strip old notes then append fresh state at the END
        state_note = self._state_note()
        prepared = self._upsert_context_note(prepared, state_note)

        tokens = estimate_messages_tokens(prepared)
        # MicroCompact (P2): stub old tool payloads before expensive full fold
        micro_gate = int(self.token_budget * 0.75)
        if tokens > micro_gate:
            bare = self._strip_ephemeral_notes(prepared)
            keep = int(self._guideline.get("microcompact_keep_recent_tools") or 4)
            stub_lim = int(self._guideline.get("stub_limit") or 180)
            compacted, n_stubs = microcompact_messages(
                bare,
                keep_recent_tools=keep,
                stub_limit=stub_lim,
            )
            if n_stubs:
                self.state.microcompact_events += n_stubs
                prepared = self._upsert_context_note(compacted, self._state_note())
                tokens = estimate_messages_tokens(prepared)

        if tokens <= self.token_budget:
            return prepared

        return self._fold_history(prepared, tokens)

    def _strip_ephemeral_notes(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            content = m.get("content")
            if m.get("role") == "user" and isinstance(content, str):
                if content.startswith(CONTEXT_NOTE_MARKER):
                    continue
                if content.startswith(ROOT_REINJECT_MARKER):
                    continue
                if content.startswith("[system note] Context folded"):
                    continue
            out.append(m)
        return out

    def _state_note(self, *, after_fold: bool = False) -> str:
        parts = [
            CONTEXT_NOTE_MARKER,
            self.state.render_current_state(),
        ]
        hist = self.state.render_history()
        if hist:
            parts.append(hist)
        if after_fold:
            root = self._root_state_block()
            if root:
                parts.append(root)
        parts.append(
            f"(layout={self.state.layout_mode}; budget ≈ {self.token_budget} tokens; "
            f"observation compressions: {self.state.compress_events}; "
            f"phase compressions: {self.state.phase_compress_events}; "
            f"microcompacts: {self.state.microcompact_events}; "
            f"folds: {self.state.fold_events}; "
            f"guideline_updates: {self.state.guideline_updates})"
        )
        return "\n\n".join(parts)

    def _root_state_block(self) -> str:
        """Force re-injection of MEMORY + focus + open todos after a fold."""
        lines = [ROOT_REINJECT_MARKER, "## Root State (authoritative after fold)"]
        if self.state.focus_files:
            lines.append("Focus files:")
            for f in self.state.focus_files:
                lines.append(f"- {f}")
        open_todos = _open_todo_lines(self.state.todos_text)
        if open_todos:
            lines.append("Incomplete todos:")
            lines.extend(open_todos)
        elif self.state.todos_text:
            lines.append("Todos: (all completed or none open)")
        mem = format_memory_section(self.state.project_memory)
        if mem:
            lines.append(mem)
        if len(lines) <= 2:
            return ""
        return "\n".join(lines)

    def _reinject_root_state(self) -> None:
        """Reload durable MEMORY.md from disk into live state before suffix render."""
        self.reload_project_memory()
        self.state.fold_events += 1

    def _upsert_context_note(
        self,
        messages: list[dict[str, Any]],
        note: str,
    ) -> list[dict[str, Any]]:
        """Place working-memory note as VARIABLE SUFFIX (end of list), not mid-prefix."""
        out: list[dict[str, Any]] = []
        for m in messages:
            content = m.get("content")
            if m.get("role") == "user" and isinstance(content, str):
                if content.startswith(CONTEXT_NOTE_MARKER):
                    continue
                if content.startswith(ROOT_REINJECT_MARKER):
                    continue
                if content.startswith("[system note] Context folded"):
                    continue
            out.append(m)
        out.append({"role": "user", "content": note})
        return out

    def _fold_history(
        self,
        messages: list[dict[str, Any]],
        tokens_before: int,
    ) -> list[dict[str, Any]]:
        """Keep system + task + recent tail; summarize the rest; re-inject root state."""
        # Strip suffix notes before measuring head/tail
        bare = [
            m
            for m in messages
            if not (
                m.get("role") == "user"
                and isinstance(m.get("content"), str)
                and (
                    m["content"].startswith(CONTEXT_NOTE_MARKER)
                    or m["content"].startswith(ROOT_REINJECT_MARKER)
                    or m["content"].startswith("[system note] Context folded")
                )
            )
        ]
        if len(bare) <= self.recent_keep_messages + 3:
            self._reinject_root_state()
            return self._upsert_context_note(
                self._shrink_tool_payloads(bare),
                self._state_note(after_fold=True),
            )

        head: list[dict[str, Any]] = []
        idx = 0
        if bare and bare[0].get("role") == "system":
            head.append(bare[0])
            idx = 1
        while idx < len(bare):
            m = bare[idx]
            if m.get("role") == "user":
                head.append(m)
                idx += 1
                break
            break

        tail_budget = self.recent_keep_messages
        tail = bare[-tail_budget:]
        while tail and tail[0].get("role") == "tool":
            start = len(bare) - len(tail) - 1
            if start < idx:
                tail = tail[1:]
                break
            tail = bare[start:]
            if tail[0].get("role") != "tool":
                break

        mid_end = len(bare) - len(tail)
        middle = bare[idx:mid_end] if mid_end > idx else []
        if middle:
            summary = self._summarize_message_slice(middle)
            self.state.history_summary = self._merge_summary(self.state.history_summary, summary)

        # P1: re-inject MEMORY + focus + incomplete todos from disk/live state
        self._reinject_root_state()

        cleaned: list[dict[str, Any]] = []
        cleaned.extend(head)
        cleaned.append(
            {
                "role": "user",
                "content": (
                    f"[system note] Context folded by Context Manager "
                    f"(≈{tokens_before} → budget {self.token_budget} tokens). "
                    "Older tool transcripts live in Historical Context; "
                    "Root State below was re-injected — trust it over the summary."
                ),
            }
        )
        for m in tail:
            cleaned.append(m)
        cleaned.append({"role": "user", "content": self._state_note(after_fold=True)})

        if estimate_messages_tokens(cleaned) > self.token_budget:
            cleaned = self._shrink_tool_payloads(cleaned)
        return cleaned

    def _shrink_tool_payloads(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.get("role") == "tool" and isinstance(m.get("content"), str):
                content = m["content"]
                if len(content) > 600:
                    m = {**m, "content": _hard_clip(content, 600)}
            out.append(m)
        return out

    def _summarize_message_slice(self, middle: list[dict[str, Any]]) -> str:
        bullets: list[str] = []
        for m in middle:
            role = m.get("role")
            if role == "assistant":
                tcs = m.get("tool_calls") or []
                names = []
                for tc in tcs:
                    fn = tc.get("function") or {}
                    names.append(str(fn.get("name") or "tool"))
                think = (m.get("content") or "").strip()
                if names:
                    bullets.append(f"called {', '.join(names)}")
                elif think:
                    bullets.append(f"thought: {think[:80]}")
            elif role == "tool":
                content = str(m.get("content") or "")
                if content.startswith("Error") or "failed" in content.lower():
                    bullets.append(f"tool result issue: {content.splitlines()[0][:100]}")
                elif content.startswith("Edited ") or content.startswith("Wrote "):
                    bullets.append(content.splitlines()[0][:120])
                elif content.startswith("Todo list:"):
                    bullets.append("updated todo list")
        # Also use action records overlapping this fold
        if not bullets and self.state.actions:
            return self._summarize_actions(self.state.actions[:-8])
        # Dedup compact
        uniq: list[str] = []
        for b in bullets:
            if b not in uniq:
                uniq.append(b)
        if not uniq:
            return f"Folded {len(middle)} older messages."
        return "Past progress:\n" + "\n".join(f"- {b}" for b in uniq[:15])

    def _summarize_actions(self, actions: list[ActionRecord]) -> str:
        if not actions:
            return ""
        lines = ["Earlier actions:"]
        for a in actions:
            path = f" on {a.path}" if a.path else ""
            status = "ok" if a.ok else "failed"
            lines.append(f"- step {a.step}: {a.tool}{path} ({status})")
        return "\n".join(lines)

    @staticmethod
    def _merge_summary(old: str, new: str) -> str:
        if not old:
            return new
        if not new:
            return old
        if new in old:
            return old
        merged = (old.rstrip() + "\n" + new.lstrip()).strip()
        # Cap summary size (progressive disclosure: keep summary short)
        if len(merged) > 2500:
            merged = merged[-2500:]
            merged = "…\n" + merged
        return merged

    def export_memory(self) -> dict[str, Any]:
        """Serializable snapshot for session transcripts / long-term memory hooks."""
        return {
            "task": self.state.task,
            "focus_files": list(self.state.focus_files),
            "related_files": list(self.state.related_files),
            "last_errors": list(self.state.last_errors),
            "history_summary": self.state.history_summary,
            "todos_text": self.state.todos_text,
            "compress_events": self.state.compress_events,
            "phase_compress_events": self.state.phase_compress_events,
            "fold_events": self.state.fold_events,
            "microcompact_events": self.state.microcompact_events,
            "guideline_updates": self.state.guideline_updates,
            "layout_mode": self.state.layout_mode,
            "has_project_memory": bool(self.state.project_memory),
            "actions": [
                {
                    "step": a.step,
                    "tool": a.tool,
                    "path": a.path,
                    "ok": a.ok,
                    "summary": a.summary,
                }
                for a in self.state.actions[-30:]
            ],
        }


def _hard_clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[folded, total {len(text)} chars]"


def _parse_todo_status_map(todos_text: str) -> dict[str, str]:
    """Map todo id → status from todo_write render() output."""
    out: dict[str, str] = {}
    if not todos_text:
        return out
    for line in todos_text.splitlines():
        m = re.match(r"\s*\[([ x>\-])\]\s*\(([^)]+)\)\s*(.+)$", line)
        if not m:
            continue
        mark, item_id = m.group(1), m.group(2)
        status = {
            " ": "pending",
            "x": "completed",
            ">": "in_progress",
            "-": "cancelled",
        }.get(mark, "pending")
        out[item_id] = status
    return out


def _open_todo_lines(todos_text: str) -> list[str]:
    """Lines for incomplete todos only (pending / in_progress)."""
    lines: list[str] = []
    if not todos_text:
        return lines
    for line in todos_text.splitlines():
        m = re.match(r"\s*\[([ >\-])\]\s*\(([^)]+)\)\s*(.+)$", line)
        if not m:
            continue
        mark, item_id, content = m.group(1), m.group(2), m.group(3).strip()
        status = {" ": "pending", ">": "in_progress", "-": "cancelled"}.get(mark)
        if status in {"pending", "in_progress"}:
            lines.append(f"- ({item_id}) [{status}] {content}")
    return lines


def _phase_bullets(actions: list[ActionRecord], *, limit: int = 5) -> list[str]:
    """Turn a phase's tool trail into 3–5 short bullets for history_summary."""
    bullets: list[str] = []
    for a in actions:
        if a.tool == "todo_write":
            continue
        path = f" {a.path}" if a.path else ""
        mark = "ok" if a.ok else "failed"
        line = f"{a.tool}{path} ({mark})"
        detail = a.summary.strip()
        if detail and detail.lower() not in line.lower():
            # Keep bullet short
            if len(detail) > 80:
                detail = detail[:77] + "…"
            line = f"{line}: {detail}"
        if line not in bullets:
            bullets.append(line)
        if len(bullets) >= limit:
            break
    return bullets


# Back-compat re-export used by older imports
def build_system_prompt(workdir: Path, tool_names: list[str]) -> str:
    return ContextManager(workdir=workdir, tool_names=tool_names).build_system_prompt()
