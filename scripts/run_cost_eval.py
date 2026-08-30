"""Cost eval runner (Cost-C).

Offline (no API): budget hard-stop / warn / cost_report / no-false-kill fixtures.
Live (needs API): fix-greeter tokens baseline + optional low-budget stop.

Usage:
  python -m scripts.run_cost_eval --offline
  python -m scripts.run_cost_eval --live
  python -m scripts.run_cost_eval --live --low-budget 8000
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.cost import CostReport, score_cost_offline
from evals.tasks import get_task
from evals.workspace import prepare_workdir, tests_are_green


def _print_table(rows: list[CostReport]) -> None:
    headers = [
        "case",
        "ok",
        "steps",
        "llm",
        "tok",
        "tools",
        "early",
        "false_kill",
        "stopped",
    ]
    print("\n=== Cost Eval ===")
    print(" | ".join(headers))
    print("-" * 108)
    for r in rows:
        print(
            " | ".join(
                [
                    r.case_id,
                    "Y" if r.success else "N",
                    str(r.steps if r.steps is not None else "-"),
                    str(r.llm_calls if r.llm_calls is not None else "-"),
                    str(r.tokens_total_est if r.tokens_total_est is not None else "-"),
                    str(r.tool_calls_total if r.tool_calls_total is not None else "-"),
                    "Y" if r.budget_stop_early else "N",
                    "Y" if r.false_budget_kill else "N",
                    r.stopped_reason or "-",
                ]
            )
        )
    print()


def run_offline() -> list[CostReport]:
    rows = score_cost_offline()
    for r in rows:
        if not r.success:
            raise AssertionError(f"cost fixture failed: {r.case_id} notes={r.notes}")
    return rows


def _run_live_fix_greeter(*, max_task_tokens: int) -> CostReport:
    from src.agent.context import build_system_prompt
    from src.agent.loop import run_agent
    from src.agent.permissions import PermissionGate
    from src.agent.transcript import save_transcript
    from src.config import Config
    from src.llm.client import LLMClient
    from src.tools import build_default_registry

    task = get_task("fix-greeter")
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp) / "ws"
        prepare_workdir(workdir, plant_greeter_bug=task.plant_greeter_bug)

        config = Config.from_env(
            workdir=str(workdir),
            max_steps=task.max_steps,
            approval="auto",
            transcript_dir=str(Path(tmp) / "transcripts"),
            max_task_tokens=max_task_tokens,
        )
        gate = PermissionGate(config.workdir, approval=config.approval)
        registry = build_default_registry(
            gate,
            max_output_chars=config.max_tool_output_chars,
            transcript_dir=config.transcript_dir,
        )
        system_prompt = build_system_prompt(config.workdir, registry.names())
        client = LLMClient(config)

        print(
            f"\n[live] fix-greeter max_task_tokens={max_task_tokens or 'off'} "
            f"model={config.model}"
        )
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
            max_task_tokens=max_task_tokens,
        )
        if config.transcript_dir is not None:
            path = save_transcript(
                config.transcript_dir,
                task=task.prompt,
                result=result,
                meta={
                    "eval": "cost",
                    "max_task_tokens": max_task_tokens,
                    "model": config.model,
                },
            )
            print(f"[live] transcript={path}")

        cr = (result.memory or {}).get("cost_report") or {}
        tokens = cr.get("tokens_total_est")
        tools = cr.get("tool_calls_total")
        green = tests_are_green(workdir) if task.require_tests_green else None

        if max_task_tokens > 0:
            early = result.stopped_reason == "budget_exhausted"
            # Low budget: must hard-stop; may or may not finish greeter
            ok = early
            notes = [
                f"low budget={max_task_tokens}",
                f"tests_green={green}",
                *( [] if early else ["FAIL: expected budget_exhausted"] ),
            ]
            return CostReport(
                case_id="cost:live-low-budget-stop",
                success=ok,
                stopped_reason=result.stopped_reason,
                steps=result.steps,
                llm_calls=cr.get("llm_calls"),
                tokens_total_est=tokens,
                tool_calls_total=tools,
                budget_stop_early=early,
                notes=notes,
            )

        # Baseline: gate off — expect completed + green, record tokens
        false_kill = result.stopped_reason == "budget_exhausted"
        ok = (
            not false_kill
            and result.stopped_reason in {"completed", "goal_met_forced"}
            and green is True
            and tokens is not None
        )
        notes = [
            "baseline gate=off",
            f"tests_green={green}",
            f"cost_summary={cr.get('summary')}",
        ]
        if not ok:
            notes.append(f"FAIL stopped={result.stopped_reason} green={green}")
        return CostReport(
            case_id="cost:live-fix-greeter-baseline",
            success=ok,
            stopped_reason=result.stopped_reason,
            steps=result.steps,
            llm_calls=cr.get("llm_calls"),
            tokens_total_est=tokens,
            tool_calls_total=tools,
            false_budget_kill=false_kill,
            notes=notes,
        )


def run_live(*, low_budget: int | None = None) -> list[CostReport]:
    rows = [_run_live_fix_greeter(max_task_tokens=0)]
    if low_budget is not None and low_budget > 0:
        rows.append(_run_live_fix_greeter(max_task_tokens=low_budget))
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Cost eval (Cost-C)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--offline",
        action="store_true",
        help="No API: budget stop / warn / cost_report fixtures",
    )
    g.add_argument(
        "--live",
        action="store_true",
        help="Call the real model: fix-greeter token baseline (+ optional low budget)",
    )
    p.add_argument(
        "--low-budget",
        type=int,
        default=None,
        help="Live only: also run with MAX_TASK_TOKENS=N expecting budget_exhausted",
    )
    p.add_argument(
        "--json-out",
        default=None,
        help="Optional JSON report path",
    )
    args = p.parse_args(argv)

    if args.offline:
        rows = run_offline()
        mode = "offline"
    else:
        rows = run_live(low_budget=args.low_budget)
        mode = "live"

    _print_table(rows)
    for r in rows:
        if r.notes:
            print(f"[{r.case_id}] " + "; ".join(r.notes))

    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "results": [r.to_dict() for r in rows],
    }
    out = args.json_out
    if out is None and mode == "live":
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = str(Path("evals") / "results" / f"cost_live_{stamp}.json")
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[json] {path}  ← copy tokens_total_est into quality doc §13")
    elif mode == "offline":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = Path("evals") / "results" / f"cost_{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[json] {path}")

    ok = all(r.success for r in rows)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
