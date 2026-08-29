"""Shared helper to construct and run one agent task + demo fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from src.agent.context import build_system_prompt
from src.agent.loop import AgentResult, run_agent
from src.agent.permissions import ApprovalMode, PermissionGate
from src.agent.transcript import save_transcript
from src.config import Config
from src.llm.client import LLMClient
from src.tools import build_default_registry

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
) -> tuple[AgentResult, Config, Path | None]:
    config = Config.from_env(
        workdir=workdir,
        model=model,
        max_steps=max_steps,
        approval=approval,
        transcript_dir=None if save_run_transcript else "off",
    )

    gate = PermissionGate(config.workdir, approval=config.approval)
    if gate.approval == ApprovalMode.ASK:
        gate = PermissionGate(config.workdir, approval=ApprovalMode.AUTO)

    registry = build_default_registry(gate, max_output_chars=config.max_tool_output_chars)
    system_prompt = build_system_prompt(config.workdir, registry.names())
    client = LLMClient(config)

    result = run_agent(
        client=client,
        registry=registry,
        system_prompt=system_prompt,
        user_task=task,
        max_steps=config.max_steps,
        gate=gate,
        max_messages=config.max_messages,
        log=log,
        on_event=on_event,
    )

    transcript_path: Path | None = None
    if save_run_transcript and config.transcript_dir is not None:
        transcript_path = save_transcript(
            config.transcript_dir,
            task=task,
            result=result,
            meta={
                "model": config.model,
                "workdir": str(config.workdir),
                "approval": gate.approval.value,
                "source": "web",
            },
        )
    return result, config, transcript_path


def list_recent_transcripts(limit: int = 12) -> list[dict[str, Any]]:
    root = Path("transcripts")
    if not root.is_dir():
        return []
    files = sorted(root.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    items: list[dict[str, Any]] = []
    for path in files[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            task = str(data.get("task") or path.stem)
            items.append(
                {
                    "id": path.name,
                    "title": task if len(task) <= 48 else task[:45] + "...",
                    "task": task,
                    "stopped_reason": data.get("stopped_reason"),
                    "created_at": data.get("created_at"),
                }
            )
        except (OSError, ValueError, KeyError):
            continue
    return items


def get_transcript(transcript_id: str) -> dict[str, Any] | None:
    """Load one transcript by file name (e.g. run_....json)."""
    name = Path(transcript_id).name
    if not name.startswith("run_") or not name.endswith(".json"):
        return None
    path = Path("transcripts") / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def reset_demo_files() -> dict[str, Any]:
    """Restore intentional bugs so demos remain reproducible."""
    demos = PROJECT_ROOT / "demos"
    written: list[str] = []
    mapping = {
        demos / "greeter.py": GREETER_BUGGY,
        demos / "buggy_calc.py": BUGGY_CALC,
    }
    for path, content in mapping.items():
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"))
    return {"reset": written}
