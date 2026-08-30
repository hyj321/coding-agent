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
from src.agent.retry_policy import RetryPolicy
from src.agent.skills import format_skills_catalog
from src.agent.task_state import TaskState
from src.tools.filesystem import snapshot_workdir

CONTEXT_NOTE_MARKER = "[Context Manager — layered working memory]"
ROOT_REINJECT_MARKER = "[Root State — re-injected after context fold]"


def context_usage_report(
    *,
    used_tokens: int,
    budget_tokens: int,
    fold_events: int = 0,
    scope: str = "turn",
) -> dict[str, Any]:
    """Shared shape for SSE / UI: remaining capacity percentage."""
    budget = max(1, int(budget_tokens))
    used = max(0, int(used_tokens))
    used_pct = min(100, int(round(100.0 * used / budget)))
    remaining_pct = max(0, 100 - used_pct)
    if remaining_pct <= 15:
        level = "critical"
        hint = "上下文将满：建议先让我总结关键结构/变量，或开 New task。"
    elif remaining_pct <= 35:
        level = "warn"
        hint = "上下文偏紧：复杂任务可先总结再继续。"
    else:
        level = "ok"
        hint = "上下文余量充足。"
    return {
        "scope": scope,
        "used_tokens": used,
        "budget_tokens": budget,
        "used_pct": used_pct,
        "remaining_pct": remaining_pct,
        "level": level,
        "folded": fold_events > 0,
        "hint": hint,
    }

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
    # Cost-B: injected each step from TaskBudget.format_line(...)
    budget_line: str = ""
    # X3: path|offset|limit → {mtime, summary, chars} for soft-dedup reads
    read_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    soft_dedup_events: int = 0

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
        if self.budget_line:
            lines.append(self.budget_line)
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
        has_body = bool(
            self.focus_files
            or self.related_files
            or self.last_errors
            or self.todos_text
            or self.project_memory
        )
        if not has_body and not self.budget_line:
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
        token_budget: int = 32000,
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
        self.task_state = TaskState()
        self.retry_policy = RetryPolicy.from_env()
        self.post_nudge_mutating: int = 0
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
        ts_raw = snapshot.get("task_state")
        if isinstance(ts_raw, dict):
            self.task_state = TaskState.from_dict(ts_raw)
            if not self.task_state.goal and self.state.task:
                self.task_state.goal = self.state.task[:500]
            # Keep relevant_files in sync with focus if empty
            if not self.task_state.relevant_files and self.state.focus_files:
                self.task_state.relevant_files = list(self.state.focus_files)[:8]
        elif self.state.task and not self.task_state.goal:
            self.task_state = TaskState.from_goal(self.state.task)
        rp_raw = snapshot.get("retry_policy")
        if isinstance(rp_raw, dict):
            self.retry_policy = RetryPolicy.from_dict(rp_raw)
            self._sync_task_retry_fields()
        self.post_nudge_mutating = int(snapshot.get("post_nudge_mutating") or 0)

    def _sync_task_retry_fields(self) -> None:
        """Mirror RetryPolicy into TaskState for Current State / working_memory."""
        self.task_state.failed = [s.to_dict() for s in self.retry_policy.by_key.values()]
        self.task_state.retry_stage = int(self.retry_policy.last_stage or 0)

    def build_system_prompt(self) -> str:
        """Stable within a run — cached so prompt-prefix bytes do not churn each step."""
        if self._system_prompt_cache is not None:
            return self._system_prompt_cache
        snapshot = snapshot_workdir(self.workdir)
        tools_line = ", ".join(self.tool_names)
        skills_block = format_skills_catalog()
        skills_section = f"\n{skills_block}\n" if skills_block else ""
        self._system_prompt_cache = f"""You are a coding agent running on the user's machine.
You solve programming tasks by calling tools.

## System Context
1. Working directory: {self.workdir}
2. Paths are relative to that directory unless stated otherwise.
3. Available tools: {tools_line}
4. Prefer edit_file for small edits; write_file for create/rewrite.
5. Prefer run_tests (target=*_test.py) over run_shell when verifying tests —
   it returns structured exit_code/passed for Completion evidence. Use run_shell
   for other non-interactive commands. Use git_status / git_diff (read-only) to
   inspect changes — do not use shell for plain git status/diff.
6. Plan-then-Act (coarse): for non-trivial multi-step tasks, call todo_write with
   3–5 phase-level items (e.g. locate → fix → verify)—NOT one todo per file or
   per tool call. Keep at most one item in_progress. Update todo_write only at
   phase boundaries (complete a phase, change plan, or finish)—not after every tool.
   Trivial one-shot edits (single obvious file, no investigation) may skip todo_write.
7. Batch tools in ONE assistant turn whenever independent: e.g. todo_write +
   grep/glob/read_file together; multiple read_file/glob/grep calls together; load_skill
   with the first reads. Never waste a turn on todo_write alone when you already
   know what to read or edit next. Dependent calls stay sequential across turns
   (e.g. run_shell then edit based on its output).
8. When done, give a clear final answer and stop calling tools.
9. Reply to the user in Simplified Chinese (简体中文) for FINAL answers,
    summaries, and explanations unless they explicitly ask for another language.
    Keep tool names, paths, and code in their original form.
10. On tool errors, adjust approach briefly and retry—do not repeat the exact
    same tool+arguments unchanged. Fingerprints listed as Failed / BLOCKED are
    hard-blocked at dispatch — change args or tool.
11. Project Memory (MEMORY.md) may appear under Current State — treat it as
    durable cross-run notes (conventions / past pitfalls); do not ignore it.
12. Use memory_search for keyword recall; rag_search for local TF–IDF semantic recall.
13. Prompt layout: system+task are a stable prefix; working memory is a variable suffix.
14. If an Available Skill matches the task, call load_skill(name) early (same turn
    as first reads when possible), then follow it.

## Decision discipline (goal–state)
D1. Before each tool call, read Current State: goal, evidence (tests/diff/todos),
    and Failed strategies — decide from that state, not from memory of prior turns alone.
D2. Never repeat a tool+args fingerprint that is Failed/BLOCKED; change arguments,
    switch tools, or ask the user.
D3. Do not claim the task is done without verification evidence (prefer run_tests
    green / agreed acceptance). Empty claims will be rejected by CompletionGate.

## Search-first (locate before bulk read)
A. To find symbols, error strings, or failing assertions: call grep and/or glob
   BEFORE whole-tree list_dir or full-file reads.
B. After a grep hit: read_file with offset/limit around that line — do not re-read
   the entire file. Long files without offset return only an auto-head window.
C. Blind list_dir of the whole repo + full-file read without a search target is an
   anti-pattern; use list_dir only when you need directory structure, not content.
{skills_section}
## Progressive Disclosure
- Do NOT paste entire files into your reasoning.
- Start from summaries / Current State / Historical Context below.
- Prefer grep to locate, then read_file with offset/limit for the slice you need.
- Relevant files are hinted in Current State — prioritize those over the whole repo.

## Workspace snapshot (top-level only; frozen at run start)
{snapshot}
"""
        return self._system_prompt_cache

    def _read_cache_key(self, args: dict[str, Any]) -> str:
        path = str(args.get("path") or "").replace("\\", "/").strip()
        off = args.get("offset")
        lim = args.get("limit")
        return f"{path}|o={off if off is not None else ''}|l={lim if lim is not None else ''}"

    def _path_mtime(self, rel: str) -> float | None:
        if not rel:
            return None
        try:
            candidate = (self.workdir / rel).resolve()
            candidate.relative_to(self.workdir.resolve())
            if not candidate.is_file():
                return None
            return float(candidate.stat().st_mtime)
        except (OSError, ValueError):
            return None

    def _invalidate_read_cache(self, rel: str | None) -> None:
        if not rel:
            return
        prefix = rel.replace("\\", "/").strip() + "|"
        drop = [k for k in self.state.read_cache if k.startswith(prefix)]
        for k in drop:
            self.state.read_cache.pop(k, None)

    def _try_soft_dedup_read(
        self,
        args: dict[str, Any],
        result: str,
    ) -> str | None:
        """If same path+slice already read and mtime unchanged, return short reuse note."""
        if str(result).startswith("Error"):
            return None
        key = self._read_cache_key(args)
        path = str(args.get("path") or "").replace("\\", "/").strip()
        mtime = self._path_mtime(path)
        if mtime is None:
            return None
        prev = self.state.read_cache.get(key)
        if (
            isinstance(prev, dict)
            and prev.get("mtime") == mtime
            and isinstance(prev.get("summary"), str)
            and prev["summary"]
        ):
            self.state.soft_dedup_events += 1
            summary = prev["summary"]
            chars = int(prev.get("chars") or 0)
            return (
                f"[soft-dedup] `{path}` unchanged (mtime={mtime:.0f}); "
                f"reusing prior read ({chars}c). Prior excerpt:\n{summary}"
            )
        return None

    def _store_read_cache(self, args: dict[str, Any], compressed: str) -> None:
        if str(compressed).startswith("Error") or compressed.startswith("[soft-dedup]"):
            return
        key = self._read_cache_key(args)
        path = str(args.get("path") or "").replace("\\", "/").strip()
        mtime = self._path_mtime(path)
        if mtime is None:
            return
        flat = " ".join(compressed.split())
        excerpt = flat[:280] + ("…" if len(flat) > 280 else "")
        self.state.read_cache[key] = {
            "mtime": mtime,
            "summary": excerpt,
            "chars": len(compressed),
        }
        # Cap cache size
        if len(self.state.read_cache) > 32:
            for old in list(self.state.read_cache.keys())[:8]:
                self.state.read_cache.pop(old, None)

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

        # X3: soft-dedup unchanged file reads before full compress
        soft_hit: str | None = None
        if tool_name == "read_file" and isinstance(raw_args, dict):
            soft_hit = self._try_soft_dedup_read(raw_args, result)

        if soft_hit is not None:
            compressed = soft_hit
        else:
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
        if soft_hit is None and tool_name == "read_file" and isinstance(raw_args, dict) and ok:
            self._store_read_cache(raw_args, compressed)
        if ok and tool_name in {"write_file", "edit_file"} and isinstance(raw_args, dict):
            self._invalidate_read_cache(str(raw_args.get("path") or ""))

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

        # Keep Task State in sync (goal / files / last_error / test_status)
        self.task_state.update_from_tool(
            tool_name=tool_name,
            args=raw_args if isinstance(raw_args, dict) else None,
            result=compressed,
            paths=paths,
        )
        if self.state.focus_files and not self.task_state.relevant_files:
            self.task_state.relevant_files = list(self.state.focus_files)[:8]
        elif self.state.focus_files:
            # Prefer focus order for relevant_files display
            for p in reversed(list(self.state.focus_files)):
                self.task_state.note_file(p)
        if self.state.last_errors and not self.task_state.last_error:
            self.task_state.last_error = self.state.last_errors[0]
        elif self.state.last_errors:
            self.task_state.last_error = self.state.last_errors[0]
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
        self.ensure_task_goal(user_task)
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
            return sanitize_tool_pairing(prepared)

        return sanitize_tool_pairing(self._fold_history(prepared, tokens))

    def usage_report(
        self,
        messages: list[dict[str, Any]] | None = None,
        *,
        scope: str = "turn",
    ) -> dict[str, Any]:
        """Estimate how full the context window is (chars≈tokens*4 heuristic)."""
        msgs = messages if messages is not None else []
        used = estimate_messages_tokens(msgs) if msgs else 0
        return context_usage_report(
            used_tokens=used,
            budget_tokens=self.token_budget,
            fold_events=self.state.fold_events,
            scope=scope,
        )

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
            self._render_current_state_with_task(),
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

    def _render_current_state_with_task(self) -> str:
        """Current State + Task State + failed strategies for prompt injection."""
        self._sync_task_retry_fields()
        base = self.state.render_current_state()
        block = self.task_state.render_block()
        extra = self.retry_policy.render_block()
        parts: list[str] = []
        if block:
            parts.append(block)
        elif extra:
            parts.append(extra)
        if not parts:
            return base
        lines = base.splitlines()
        if lines and lines[0].startswith("## Current State"):
            return "\n".join([lines[0], *parts, *lines[1:]])
        return base + "\n" + "\n".join(parts)

    def ensure_task_goal(self, user_task: str, *, replace: bool = False) -> None:
        """Set / refresh goal from the latest user task.

        ``replace=True`` on each new user turn so hydrated working_memory does not
        keep a stale goal (e.g. prior \"改成输出3\") that skews CompletionGate.
        """
        text = (user_task or "").strip()
        if text:
            self.state.task = text
        elif user_task is not None:
            self.state.task = user_task or self.state.task
        if text and (replace or not self.task_state.goal):
            self.task_state.goal = text[:500]

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
            return sanitize_tool_pairing(
                self._upsert_context_note(
                    self._shrink_tool_payloads(bare),
                    self._state_note(after_fold=True),
                )
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
        tail = expand_tail_for_tool_pairing(bare, tail, min_index=idx)

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

        cleaned = sanitize_tool_pairing(cleaned)
        if estimate_messages_tokens(cleaned) > self.token_budget:
            cleaned = self._shrink_tool_payloads(cleaned)
            cleaned = sanitize_tool_pairing(cleaned)
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
            "soft_dedup_events": self.state.soft_dedup_events,
            "fold_events": self.state.fold_events,
            "microcompact_events": self.state.microcompact_events,
            "guideline_updates": self.state.guideline_updates,
            "layout_mode": self.state.layout_mode,
            "has_project_memory": bool(self.state.project_memory),
            "task_state": self.task_state.to_dict(),
            "retry_policy": self.retry_policy.to_dict(),
            "post_nudge_mutating": self.post_nudge_mutating,
            "stop_nudge_reasons": list(self.task_state.stop_nudge_reasons),
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


def sanitize_tool_pairing(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure every `tool` message follows an assistant with matching tool_calls.

    Prevents API 400 when fold/trim/continue leaves orphan tool rows.
    """
    out: list[dict[str, Any]] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        role = m.get("role")

        if role == "tool":
            i += 1
            continue

        if role != "assistant":
            out.append(m)
            i += 1
            continue

        tool_calls = m.get("tool_calls") or []
        if not tool_calls:
            out.append(m)
            i += 1
            continue

        expected: dict[str, dict[str, Any]] = {}
        for tc in tool_calls:
            tid = tc.get("id")
            if tid:
                expected[str(tid)] = tc

        j = i + 1
        tools: list[dict[str, Any]] = []
        while j < n and messages[j].get("role") == "tool":
            tools.append(messages[j])
            j += 1

        matched_tools = [
            t
            for t in tools
            if t.get("tool_call_id") is not None
            and str(t.get("tool_call_id")) in expected
        ]
        matched_ids = {str(t.get("tool_call_id")) for t in matched_tools}

        if matched_tools and matched_ids == set(expected.keys()):
            out.append(m)
            out.extend(matched_tools)
        elif matched_tools:
            order = [str(tc.get("id")) for tc in tool_calls if tc.get("id")]
            kept_calls = [expected[tid] for tid in order if tid in matched_ids]
            adj = dict(m)
            adj["tool_calls"] = kept_calls
            out.append(adj)
            id_order = {tid: k for k, tid in enumerate(order)}
            matched_tools.sort(
                key=lambda t: id_order.get(str(t.get("tool_call_id")), 999)
            )
            out.extend(matched_tools)
        else:
            adj = {k: v for k, v in m.items() if k != "tool_calls"}
            if not adj.get("content"):
                adj["content"] = "(tool calls omitted during context compaction)"
            out.append(adj)

        i = j

    return out


def expand_tail_for_tool_pairing(
    messages: list[dict[str, Any]],
    tail: list[dict[str, Any]],
    *,
    min_index: int = 0,
) -> list[dict[str, Any]]:
    """Grow or shrink `tail` so it does not start with an orphan `tool` message."""
    if not tail:
        return tail
    while tail and tail[0].get("role") == "tool":
        start = len(messages) - len(tail) - 1
        if start < min_index:
            tail = tail[1:]
            continue
        tail = messages[start:]
        if tail[0].get("role") != "tool":
            break
    return sanitize_tool_pairing(list(tail)) if tail else tail


# Back-compat re-export used by older imports
def build_system_prompt(workdir: Path, tool_names: list[str]) -> str:
    return ContextManager(workdir=workdir, tool_names=tool_names).build_system_prompt()
