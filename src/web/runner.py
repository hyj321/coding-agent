"""Shared helper to construct and run one agent task + demo fixtures."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from src.agent.context import build_system_prompt
from src.agent.compress import estimate_messages_tokens
from src.agent.context_manager import context_usage_report
from src.agent.loop import AgentResult, run_agent
from src.agent.permissions import ApprovalMode, AskFn, PermissionGate
from src.agent.transcript import (
    append_session_file_changes,
    build_continue_context,
    load_session,
    resolve_continue_task,
    save_transcript,
)
from src.config import Config
from src.llm.client import LLMClient
from src.tools import build_default_registry
from src.tools.fs_noise import is_agent_scratch, is_noise_entry

LogFn = Callable[[str], None]
EventFn = Callable[[dict[str, Any]], None]

PROJECT_ROOT = Path(__file__).resolve().parents[2]

GREETER_BUGGY = '''"""Demo greeter with an intentional bug (for Day3 video / acceptance)."""


def greet(name: str) -> str:
    # BUG: should return "Hello, {name}!"
    return f"Hi {name}"
'''

BUGGY_CALC = '''"""Intentional bug for Day2 edit_file / self-repair demo."""


def add(a: int, b: int) -> int:
    # BUG: should return a + b
    return a - b


if __name__ == "__main__":
    assert add(2, 3) == 5, f"expected 5, got {add(2, 3)}"
    print("ok")
'''

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")


def resolve_workdir(workdir: str | Path | None) -> Path:
    raw = Path(workdir) if workdir else PROJECT_ROOT / "demos"
    if not raw.is_absolute():
        raw = (PROJECT_ROOT / raw).resolve()
    else:
        raw = raw.resolve()
    return raw


def list_directory(path: Path, *, max_entries: int = 200) -> dict[str, Any]:
    """List one directory for Open Folder / file explorer."""
    if not path.exists():
        raise FileNotFoundError(f"path not found: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"not a directory: {path}")
    entries: list[dict[str, Any]] = []
    children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    for child in children:
        if is_noise_entry(child.name):
            continue
        try:
            entries.append(
                {
                    "name": child.name,
                    "path": str(child.resolve()),
                    "relative": child.name,
                    "kind": "dir" if child.is_dir() else "file",
                }
            )
        except OSError:
            continue
        if len(entries) >= max_entries:
            break
    parent = path.parent if path.parent != path else None
    return {
        "path": str(path.resolve()),
        "parent": str(parent.resolve()) if parent is not None else None,
        "entries": entries,
        "truncated": len(children) > len(entries),
    }


def build_tree(workdir: Path, *, max_depth: int = 3, max_nodes: int = 250) -> dict[str, Any]:
    """Shallow tree under workdir for the right-side explorer."""
    root = workdir.resolve()
    nodes: list[dict[str, Any]] = []
    count = 0

    def walk(current: Path, depth: int, rel: str) -> None:
        nonlocal count
        if count >= max_nodes or depth > max_depth:
            return
        try:
            children = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        for child in children:
            if is_noise_entry(child.name):
                continue
            count += 1
            if count > max_nodes:
                return
            child_rel = child.name if not rel else f"{rel}/{child.name}"
            node = {
                "name": child.name,
                "path": child_rel.replace("\\", "/"),
                "kind": "dir" if child.is_dir() else "file",
                "depth": depth,
            }
            nodes.append(node)
            if child.is_dir() and depth < max_depth:
                walk(child, depth + 1, child_rel)

    walk(root, 0, "")
    return {"workdir": str(root), "nodes": nodes, "truncated": count >= max_nodes}


def read_workdir_file(workdir: Path, rel_path: str, *, max_chars: int = 200_000) -> dict[str, Any]:
    root = workdir.resolve()
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PermissionError("path escapes workdir") from exc
    if not target.is_file():
        raise FileNotFoundError(f"file not found: {rel_path}")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("binary or non-UTF-8 file") from exc
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + f"\n...[truncated, total chars beyond {max_chars}]"
    return {
        "path": rel_path.replace("\\", "/"),
        "workdir": str(root),
        "content": text,
        "truncated": truncated,
    }


def write_workdir_file(workdir: Path, rel_path: str, content: str) -> dict[str, Any]:
    """Write UTF-8 text under workdir (for UI undo/redo of a diff)."""
    root = workdir.resolve()
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PermissionError("path escapes workdir") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content if content is not None else "", encoding="utf-8", newline="\n")
    return {
        "path": rel_path.replace("\\", "/"),
        "workdir": str(root),
        "bytes": target.stat().st_size,
    }


def delete_workdir_file(workdir: Path, rel_path: str) -> dict[str, Any]:
    """Delete a file under workdir (undo a newly created file)."""
    root = workdir.resolve()
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PermissionError("path escapes workdir") from exc
    if not target.exists():
        return {"path": rel_path.replace("\\", "/"), "deleted": False, "reason": "missing"}
    if not target.is_file():
        raise ValueError("not a file")
    target.unlink()
    return {"path": rel_path.replace("\\", "/"), "deleted": True}


def run_coding_task(
    *,
    task: str,
    workdir: str | Path | None = None,
    model: str | None = None,
    max_steps: int | None = None,
    approval: str = "auto",
    save_run_transcript: bool = True,
    log: LogFn | None = None,
    on_event: EventFn | None = None,
    session_id: str | None = None,
    ask_fn: AskFn | None = None,
    ask_min_risk: str = "medium",
    deny_high: bool | None = None,
    cancel_event: Any | None = None,
    steer_inbox: Any | None = None,
) -> tuple[AgentResult, Config, Path | None]:
    config = Config.from_env(
        workdir=workdir,
        model=model,
        max_steps=max_steps,
        approval=approval,
        transcript_dir=None if save_run_transcript else "off",
    )

    # Web interactive: approval=ask + ask_fn; deny_high off so user can Allow High
    if ask_fn is not None:
        mode = ApprovalMode.ASK
        high_deny = False if deny_high is None else deny_high
    else:
        mode = config.approval
        # Legacy Web path without ask_fn: auto + deny high
        high_deny = True if deny_high is None else deny_high
        if mode == ApprovalMode.ASK:
            # No ask_fn (e.g. misconfigured) — fall back to auto+deny_high
            mode = ApprovalMode.AUTO
            high_deny = True if deny_high is None else deny_high

    min_risk = ask_min_risk if ask_min_risk in {"low", "medium", "high"} else "medium"

    gate = PermissionGate(
        config.workdir,
        approval=mode,
        ask_fn=ask_fn,
        deny_high=high_deny,
        ask_min_risk=min_risk,  # type: ignore[arg-type]
    )

    registry = build_default_registry(
        gate,
        max_output_chars=config.max_tool_output_chars,
        transcript_dir=config.transcript_dir,
    )
    system_prompt = build_system_prompt(config.workdir, registry.names())
    client = LLMClient(config)

    prior_messages: list[dict[str, Any]] | None = None
    prior_memory: dict[str, Any] | None = None
    sid = session_id.strip() if session_id and _SESSION_ID_RE.match(session_id.strip()) else None
    effective_task = task
    if sid and config.transcript_dir is not None:
        prev = load_session(config.transcript_dir, sid)
        if prev and prev.get("messages"):
            # Expand 「继续做」→ explicit unfinished goal; slim prior context
            effective_task = resolve_continue_task(task, prev)
            if effective_task != task and log is not None:
                log(f"[session] continue resolved → {effective_task[:120]!r}")
            prior_messages, prior_memory = build_continue_context(prev, recent_k=24)
            # Session-level pressure: how much prior already eats the budget
            if on_event is not None:
                prior_tokens = estimate_messages_tokens(prior_messages)
                on_event(
                    {
                        "type": "context_usage",
                        **context_usage_report(
                            used_tokens=prior_tokens,
                            budget_tokens=config.context_token_budget,
                            scope="session",
                        ),
                    }
                )

    file_changes: list[dict[str, Any]] = []

    def wrapped_event(event: dict[str, Any]) -> None:
        if event.get("type") == "file_change":
            file_changes.append(
                {
                    "path": event.get("path"),
                    "tool": event.get("tool"),
                    "old_content": event.get("old_content"),
                    "new_content": event.get("new_content"),
                    "is_new": event.get("is_new"),
                    "step": event.get("step"),
                }
            )
        if on_event is not None:
            on_event(event)

    result = run_agent(
        client=client,
        registry=registry,
        system_prompt=system_prompt,
        user_task=effective_task,
        max_steps=config.max_steps,
        gate=gate,
        max_messages=config.max_messages,
        log=log,
        on_event=wrapped_event,
        prior_messages=prior_messages,
        prior_memory=prior_memory,
        context_token_budget=config.context_token_budget,
        transcript_dir=config.transcript_dir,
        cancel_event=cancel_event,
        steer_inbox=steer_inbox,
    )

    transcript_path: Path | None = None
    if save_run_transcript and config.transcript_dir is not None:
        # Persist the user-visible utterance; resolved continue text is in messages
        transcript_path = save_transcript(
            config.transcript_dir,
            task=task,
            result=result,
            meta={
                "model": config.model,
                "workdir": str(config.workdir),
                "approval": gate.approval.value,
                "source": "web",
                "session_id": sid,
                "resolved_task": effective_task if effective_task != task else None,
            },
            session_id=sid,
        )
        if sid and file_changes:
            append_session_file_changes(config.transcript_dir, sid, file_changes)
    return result, config, transcript_path


def list_recent_transcripts(limit: int = 30) -> list[dict[str, Any]]:
    """List sessions (preferred) and legacy single-run transcripts as history."""
    root = Path("transcripts")
    if not root.is_dir():
        return []
    files = sorted(
        list(root.glob("session_*.json")) + list(root.glob("run_*.json")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    items: list[dict[str, Any]] = []
    for path in files:
        if len(items) >= limit:
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            task = str(data.get("task") or path.stem)
            kind = data.get("kind") or ("session" if path.name.startswith("session_") else "run")
            turns = data.get("turns") or []
            title = task if len(task) <= 48 else task[:45] + "..."
            if kind == "session" and len(turns) > 1:
                title = f"{title} · {len(turns)} turns"
            items.append(
                {
                    "id": path.name,
                    "title": title,
                    "task": task,
                    "kind": kind,
                    "session_id": data.get("session_id") or (data.get("meta") or {}).get("session_id"),
                    "turns": len(turns) if turns else 1,
                    "stopped_reason": data.get("stopped_reason"),
                    "created_at": data.get("updated_at") or data.get("created_at"),
                    "workdir": (data.get("meta") or {}).get("workdir"),
                }
            )
        except (OSError, ValueError, KeyError):
            continue
    return items


def get_transcript(transcript_id: str) -> dict[str, Any] | None:
    """Load one transcript by file name (session_*.json or run_*.json)."""
    name = Path(transcript_id).name
    if not (
        (name.startswith("run_") or name.startswith("session_")) and name.endswith(".json")
    ):
        return None
    path = Path("transcripts") / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def reset_demo_files() -> dict[str, Any]:
    """Restore intentional bugs so demos remain reproducible; purge agent scratch files."""
    import shutil

    demos = PROJECT_ROOT / "demos"
    written: list[str] = []
    mapping = {
        demos / "greeter.py": GREETER_BUGGY,
        demos / "buggy_calc.py": BUGGY_CALC,
    }
    for path, content in mapping.items():
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"))

    removed: list[str] = []
    if demos.is_dir():
        for child in list(demos.iterdir()):
            if not is_agent_scratch(child.name):
                continue
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                removed.append(child.name)
            except OSError:
                continue
    return {"reset": written, "removed_scratch": removed}
