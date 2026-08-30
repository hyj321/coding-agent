"""Capability eval runner (Cap-C).

Offline (no API): score fixtures + plant-bug / run_tests sanity.
Live (needs DEEPSEEK_API_KEY): run locate-string + fix-greeter and print a table.

Usage:
  python -m scripts.run_capability_eval --offline
  python -m scripts.run_capability_eval --live
  python -m scripts.run_capability_eval --live --task fix-greeter
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.score import (
    ToolEvent,
    extract_tool_trace,
    final_mentions_path,
    score_trace,
)
from evals.tasks import TASKS, CapabilityTask, get_task
from evals.workspace import prepare_workdir, tests_are_green


@dataclass
class TaskReport:
    task_id: str
    mode: str
    success: bool
    steps: int | None
    stopped_reason: str
    used_grep: bool
    search_first: bool
    used_run_tests: bool
    tests_green: bool | None
    notes: list[str] = field(default_factory=list)
    workdir: str = ""
    # Dec-C: pathology flags from stopped_reason (live) or 0 offline
    cycle_events: int = 0
    blocked_replays: int = 0
    pathology: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _print_table(rows: list[TaskReport]) -> None:
    headers = [
        "task",
        "ok",
        "steps",
        "grep",
        "search1st",
        "run_tests",
        "tests_green",
        "pathology",
        "stopped",
    ]
    print("\n=== Capability Eval ===")
    print(" | ".join(headers))
    print("-" * 100)
    for r in rows:
        print(
            " | ".join(
                [
                    r.task_id,
                    "Y" if r.success else "N",
                    str(r.steps if r.steps is not None else "-"),
                    "Y" if r.used_grep else "N",
                    "Y" if r.search_first else "N",
                    "Y" if r.used_run_tests else "N",
                    (
                        "Y"
                        if r.tests_green is True
                        else ("N" if r.tests_green is False else "-")
                    ),
                    str(r.pathology),
                    r.stopped_reason,
                ]
            )
        )
    print()


def run_offline() -> list[TaskReport]:
    """No LLM: metric unit tests + workspace plant/verify."""
    rows: list[TaskReport] = []

    # 1) Metric scorer — search-first positive
    good = score_trace(
        task_id="locate-string",
        steps=3,
        events=[
            ToolEvent("todo_write", {"todos": []}),
            ToolEvent("grep", {"pattern": "def greet"}),
            ToolEvent("read_file", {"path": "greeter.py", "offset": 1, "limit": 20}),
        ],
    )
    assert good.search_first and good.used_grep
    rows.append(
        TaskReport(
            task_id="score:search-first",
            mode="offline",
            success=True,
            steps=good.steps,
            stopped_reason="fixture",
            used_grep=good.used_grep,
            search_first=good.search_first,
            used_run_tests=False,
            tests_green=None,
            notes=good.notes,
        )
    )

    # 2) Metric scorer — blind list_dir first
    bad = score_trace(
        task_id="locate-string",
        steps=2,
        events=[
            ToolEvent("list_dir", {"path": "."}),
            ToolEvent("grep", {"pattern": "greet"}),
        ],
    )
    assert bad.blind_full_list_first and not bad.search_first
    rows.append(
        TaskReport(
            task_id="score:blind-list",
            mode="offline",
            success=True,
            steps=bad.steps,
            stopped_reason="fixture",
            used_grep=bad.used_grep,
            search_first=bad.search_first,
            used_run_tests=False,
            tests_green=None,
            notes=bad.notes + ["detected blind list_dir (expected)"],
        )
    )

    # 3) Plant bug → tests red; fix → tests green; run_tests tool structured
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "demos"
        prepare_workdir(root, plant_greeter_bug=True)
        assert not tests_are_green(root), "planted bug should fail tests"
        from src.agent.permissions import ApprovalMode, PermissionGate
        from src.tools import build_default_registry

        gate = PermissionGate(root, approval=ApprovalMode.AUTO)
        reg = build_default_registry(gate, max_output_chars=4000)
        fail_out = reg.dispatch(
            "run_tests", {"target": "greeter_test.py", "runner": "python"}
        )
        assert "passed: false" in fail_out
        # Apply canonical fix
        from evals.fixtures import GREETER_FIXED

        (root / "greeter.py").write_text(GREETER_FIXED, encoding="utf-8")
        assert tests_are_green(root)
        ok_out = reg.dispatch(
            "run_tests", {"target": "greeter_test.py", "runner": "python"}
        )
        assert "passed: true" in ok_out and "exit_code: 0" in ok_out
        rows.append(
            TaskReport(
                task_id="harness:fix-greeter-path",
                mode="offline",
                success=True,
                steps=None,
                stopped_reason="harness",
                used_grep=False,
                search_first=False,
                used_run_tests=True,
                tests_green=True,
                notes=["plant bug → red; fix → green via run_tests"],
                workdir=str(root),
            )
        )

    return rows


def _run_live_task(task: CapabilityTask) -> TaskReport:
    from src.agent.context import build_system_prompt
    from src.agent.loop import run_agent
    from src.agent.permissions import PermissionGate
    from src.agent.transcript import save_transcript
    from src.config import Config
    from src.llm.client import LLMClient
    from src.tools import build_default_registry

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp) / "demos"
        prepare_workdir(workdir, plant_greeter_bug=task.plant_greeter_bug)
        if task.plant_greeter_bug and tests_are_green(workdir):
            raise RuntimeError("expected planted greeter bug to fail tests")

        config = Config.from_env(
            workdir=str(workdir),
            max_steps=task.max_steps,
            approval="auto",
            transcript_dir=str(Path(tmp) / "transcripts"),
        )
        gate = PermissionGate(config.workdir, approval=config.approval)
        registry = build_default_registry(
            gate,
            max_output_chars=config.max_tool_output_chars,
            transcript_dir=config.transcript_dir,
        )
        system_prompt = build_system_prompt(config.workdir, registry.names())
        client = LLMClient(config)

        print(f"\n[live] task={task.id} workdir={workdir} model={config.model}")
        result = run_agent(
            client=client,
            registry=registry,
            system_prompt=system_prompt,
            user_task=task.prompt,
            max_steps=task.max_steps,
            gate=gate,
            max_messages=config.max_messages,
            context_token_budget=config.context_token_budget,
            transcript_dir=config.transcript_dir,
        )
        if config.transcript_dir is not None:
            path = save_transcript(
                config.transcript_dir,
                task=task.prompt,
                result=result,
                meta={"eval_task": task.id, "model": config.model},
            )
            print(f"[live] transcript={path}")

        score = score_trace(
            task_id=task.id,
            steps=result.steps,
            messages=result.messages,
        )
        from evals.decision import count_pathology_from_stopped

        patho = count_pathology_from_stopped(result.stopped_reason)
        tests_green: bool | None = None
        if task.require_tests_green:
            tests_green = tests_are_green(workdir)

        success = True
        notes = list(score.notes)
        if task.require_grep and not score.used_grep:
            success = False
            notes.append("FAIL: grep required")
        if task.require_run_tests and not score.used_run_tests:
            # Soft fail note — still allow success if tests green via shell
            notes.append("WARN: run_tests not used")
        if task.require_tests_green and not tests_green:
            success = False
            notes.append("FAIL: tests not green")
        if task.id == "locate-string":
            if not score.search_first:
                success = False
                notes.append("FAIL: search-first required")
            if not final_mentions_path(result.final_text, task.success_path_hint):
                # soft: path may only appear in tool results
                if not any(
                    task.success_path_hint in (e.args.get("path") or "")
                    for e in extract_tool_trace(result.messages)
                ):
                    success = False
                    notes.append("FAIL: greeter.py not referenced")
        # Normal capability tasks should not die of pathology (Dec-C false-injury check)
        if patho["pathology"] and result.stopped_reason != "completed":
            if task.id in {"locate-string", "fix-greeter"}:
                notes.append(
                    f"WARN: pathology stop ({result.stopped_reason}) on normal task"
                )

        return TaskReport(
            task_id=task.id,
            mode="live",
            success=success,
            steps=result.steps,
            stopped_reason=result.stopped_reason,
            used_grep=score.used_grep,
            search_first=score.search_first,
            used_run_tests=score.used_run_tests,
            tests_green=tests_green,
            notes=notes,
            workdir=str(workdir),
            cycle_events=patho["cycle_events"],
            blocked_replays=patho["blocked_replays"],
            pathology=patho["pathology"],
        )


def run_live(task_ids: list[str] | None = None) -> list[TaskReport]:
    ids = task_ids or list(TASKS.keys())
    rows: list[TaskReport] = []
    for tid in ids:
        task = get_task(tid)
        try:
            rows.append(_run_live_task(task))
        except Exception as exc:  # noqa: BLE001
            rows.append(
                TaskReport(
                    task_id=tid,
                    mode="live",
                    success=False,
                    steps=None,
                    stopped_reason="error",
                    used_grep=False,
                    search_first=False,
                    used_run_tests=False,
                    tests_green=None,
                    notes=[f"ERROR: {exc}"],
                )
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Capability eval (Cap-C)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--offline",
        action="store_true",
        help="No API: score fixtures + plant-bug/run_tests harness checks",
    )
    g.add_argument(
        "--live",
        action="store_true",
        help="Call the real model on locate-string + fix-greeter (needs API key)",
    )
    p.add_argument(
        "--task",
        action="append",
        dest="tasks",
        help="Live only: task id (repeatable). Default: all. Known: "
        + ", ".join(sorted(TASKS)),
    )
    p.add_argument(
        "--json-out",
        default=None,
        help="Write full report JSON to this path",
    )
    args = p.parse_args(argv)

    if args.offline:
        rows = run_offline()
    else:
        rows = run_live(args.tasks)

    _print_table(rows)
    for r in rows:
        if r.notes:
            print(f"[{r.task_id}] " + "; ".join(r.notes))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "offline" if args.offline else "live",
        "results": [r.to_dict() for r in rows],
    }
    out = args.json_out
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[json] {path}")
    else:
        # Always drop a baseline under evals/results for live runs
        if args.live:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = Path("evals") / "results" / f"live_{stamp}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"[json] {path}  ← record steps baseline in quality doc §13")

    ok = all(r.success for r in rows)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
