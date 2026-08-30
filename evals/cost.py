"""Cost-dimension offline scoring (Cost-C / M-$7).

Synthesizes TaskBudget + run_agent hard-gate trajectories — no LLM API required.
"""

from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.agent.loop import run_agent
from src.agent.permissions import ApprovalMode, PermissionGate
from src.agent.task_budget import TaskBudget
from src.tools import build_default_registry


@dataclass
class CostReport:
    """One offline / live cost fixture row."""

    case_id: str
    success: bool
    stopped_reason: str = ""
    steps: int | None = None
    llm_calls: int | None = None
    tokens_total_est: int | None = None
    tool_calls_total: int | None = None
    budget_stop_early: bool = False
    false_budget_kill: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fake_cfg(**kwargs: Any) -> SimpleNamespace:
    base = dict(
        model="fake",
        context_token_budget=8000,
        tool_visibility="off",
        completion_mode="trust_model",
        evidence_nudge_max=0,
        loop_warn_after=3,
        loop_stop_after=5,
        loop_error_nudge_after=2,
        retry_max_failures=3,
        final_nudge_mutating_limit=2,
        max_task_tokens=0,
        task_token_output_reserve=30,
        workdir=None,
        transcript_dir=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _case_unit_gate() -> CostReport:
    tb = TaskBudget(max_task_tokens=1000, output_reserve=100)
    ok = (
        tb.enabled
        and not tb.would_exceed(100)
        and tb.would_exceed(950)
        and TaskBudget(max_task_tokens=0).check_before_llm(
            [{"role": "user", "content": "x" * 5000}]
        )
        is None
    )
    return CostReport(
        case_id="cost:unit-gate",
        success=ok,
        notes=["enabled would_exceed + cap=off never denies"],
    )


def _case_warn_and_report() -> CostReport:
    tb = TaskBudget(max_task_tokens=1000, output_reserve=50, warn_ratio=0.2)
    tb.record_llm_turn(prompt_tokens=700, completion_tokens=100)
    tb.record_tool("read_file")
    tb.record_tool("run_tests")
    cr = tb.cost_report(steps=1, max_steps=10, stopped_reason="completed")
    w = tb.maybe_warn_message(step=1, max_steps=10)
    ok = (
        cr.get("tokens_total_est") == 800
        and cr.get("tool_counts", {}).get("read_file") == 1
        and cr.get("tool_counts", {}).get("run_tests") == 1
        and bool(w and "[budget_warn]" in w)
        and tb.maybe_warn_message(step=2, max_steps=10) is None
        and "Budget:" in tb.format_line(step=1, max_steps=10)
    )
    return CostReport(
        case_id="cost:warn-report",
        success=ok,
        tokens_total_est=cr.get("tokens_total_est"),
        tool_calls_total=cr.get("tool_calls_total"),
        notes=["≤20% one-shot warn + cost_report fields"],
    )


def _case_budget_stop_first() -> CostReport:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.txt").write_text("hi", encoding="utf-8")
        gate = PermissionGate(root, approval=ApprovalMode.AUTO)
        reg = build_default_registry(gate, max_output_chars=2000)

        class Client:
            calls = 0
            config = _fake_cfg(workdir=root, max_task_tokens=80, task_token_output_reserve=20)
            cache_policy = None

            def chat(self, *_a, **_k):
                type(self).calls += 1
                raise AssertionError("LLM should not be called")

        client = Client()
        result = run_agent(
            client=client,
            registry=reg,
            system_prompt="sys",
            user_task="long task",
            max_steps=5,
            gate=gate,
            persist_memory_md=False,
            max_task_tokens=80,
        )
        cr = (result.memory or {}).get("cost_report") or {}
        early = result.stopped_reason == "budget_exhausted" and client.calls == 0
        ok = early and result.steps == 0 and cr.get("budget_kind") == "tokens"
        return CostReport(
            case_id="cost:budget-stop-first",
            success=ok,
            stopped_reason=result.stopped_reason,
            steps=result.steps,
            llm_calls=client.calls,
            tokens_total_est=cr.get("tokens_total_est"),
            tool_calls_total=cr.get("tool_calls_total"),
            budget_stop_early=early,
            notes=["tiny MAX_TASK_TOKENS → deny before first LLM"],
        )


def _case_budget_stop_second() -> CostReport:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.txt").write_text("hi", encoding="utf-8")
        gate = PermissionGate(root, approval=ApprovalMode.AUTO)
        reg = build_default_registry(gate, max_output_chars=2000)

        class Client:
            def __init__(self) -> None:
                self.calls = 0
                self.config = _fake_cfg()
                self.cache_policy = None

            def chat(self, messages, tools=None):
                self.calls += 1
                if self.calls == 1:
                    tc = SimpleNamespace(
                        id="c1",
                        type="function",
                        function=SimpleNamespace(
                            name="list_dir",
                            arguments='{"path": "."}',
                        ),
                    )
                    msg = SimpleNamespace(
                        role="assistant",
                        content="looking",
                        tool_calls=[tc],
                    )
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=msg)], usage=None
                    )
                raise AssertionError("second LLM must not run")

        client = Client()
        result = run_agent(
            client=client,
            registry=reg,
            system_prompt="sys",
            user_task="list then stop",
            max_steps=5,
            gate=None,
            persist_memory_md=False,
            max_task_tokens=80,
            context_manager=None,
        )
        cr = (result.memory or {}).get("cost_report") or {}
        early = (
            result.stopped_reason == "budget_exhausted"
            and client.calls == 1
            and result.steps == 1
        )
        ok = (
            early
            and cr.get("tool_counts", {}).get("list_dir") == 1
            and (cr.get("tokens_total_est") or 0) > 0
        )
        return CostReport(
            case_id="cost:budget-stop-second",
            success=ok,
            stopped_reason=result.stopped_reason,
            steps=result.steps,
            llm_calls=client.calls,
            tokens_total_est=cr.get("tokens_total_est"),
            tool_calls_total=cr.get("tool_calls_total"),
            budget_stop_early=early,
            notes=["accumulate then deny before second LLM"],
        )


def _case_gate_off_no_false_kill() -> CostReport:
    """max_task_tokens=0: first LLM allowed (no budget_exhausted)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.txt").write_text("hi", encoding="utf-8")
        gate = PermissionGate(root, approval=ApprovalMode.AUTO)
        reg = build_default_registry(gate, max_output_chars=2000)

        class Client:
            def __init__(self) -> None:
                self.calls = 0
                self.config = _fake_cfg(workdir=root)
                self.cache_policy = None

            def chat(self, messages, tools=None):
                self.calls += 1
                msg = SimpleNamespace(
                    role="assistant",
                    content="done without tools",
                    tool_calls=None,
                )
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)

        client = Client()
        result = run_agent(
            client=client,
            registry=reg,
            system_prompt="sys",
            user_task="say done",
            max_steps=3,
            gate=gate,
            persist_memory_md=False,
            max_task_tokens=0,
        )
        false_kill = result.stopped_reason == "budget_exhausted"
        ok = (
            not false_kill
            and client.calls == 1
            and result.stopped_reason == "completed"
            and (result.memory or {}).get("cost_report", {}).get("budget_enabled") is False
        )
        return CostReport(
            case_id="cost:gate-off-no-false-kill",
            success=ok,
            stopped_reason=result.stopped_reason,
            steps=result.steps,
            llm_calls=client.calls,
            tokens_total_est=(result.memory or {}).get("cost_report", {}).get(
                "tokens_total_est"
            ),
            false_budget_kill=false_kill,
            notes=["MAX_TASK_TOKENS=0 must not kill normal runs"],
        )


def score_cost_offline() -> list[CostReport]:
    return [
        _case_unit_gate(),
        _case_warn_and_report(),
        _case_budget_stop_first(),
        _case_budget_stop_second(),
        _case_gate_off_no_false_kill(),
    ]
