"""Unified improvement suite (Imp-A / I1).

Aggregates Capability + Decision + Cost offline (and optional live Cap rows)
plus Verification / Security fixture rows into one comparable table.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.agent.completion_gate import (
    is_fake_green,
    should_block_completion,
)
from src.agent.permissions import ApprovalMode, PermissionGate, assess_shell_risk
from src.agent.task_state import TaskState, TestStatus


@dataclass
class SuiteRow:
    dim: str
    task: str
    mode: str
    ok: bool
    completed: bool
    steps: int | None
    stopped_reason: str
    violated: bool = False
    pathology: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SuiteSummary:
    n: int
    ok_n: int
    completed_n: int
    violated_n: int
    pathology_n: int
    ok_rate: float
    completed_rate: float
    violation_rate: float
    pathology_rate: float
    avg_steps: float | None
    all_ok: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize(rows: list[SuiteRow]) -> SuiteSummary:
    n = len(rows)
    ok_n = sum(1 for r in rows if r.ok)
    completed_n = sum(1 for r in rows if r.completed)
    violated_n = sum(1 for r in rows if r.violated)
    pathology_n = sum(1 for r in rows if r.pathology)
    steps_vals = [r.steps for r in rows if r.steps is not None]
    avg = (sum(steps_vals) / len(steps_vals)) if steps_vals else None
    return SuiteSummary(
        n=n,
        ok_n=ok_n,
        completed_n=completed_n,
        violated_n=violated_n,
        pathology_n=pathology_n,
        ok_rate=(ok_n / n) if n else 0.0,
        completed_rate=(completed_n / n) if n else 0.0,
        violation_rate=(violated_n / n) if n else 0.0,
        pathology_rate=(pathology_n / n) if n else 0.0,
        avg_steps=avg,
        all_ok=ok_n == n and n > 0,
    )


def _ver_rows() -> list[SuiteRow]:
    rows: list[SuiteRow] = []

    # Missing tests → block (completed=False is expected; ok if gate blocks)
    ts = TaskState(
        goal="修复 greeter",
        stop_condition="tests_all_pass",
        files_mutated=True,
    )
    ts.note_mutation("greeter.py")
    block, why = should_block_completion(ts, completion_mode="evidence", max_nudges=2)
    ok = block and "evidence" in why
    rows.append(
        SuiteRow(
            dim="verify",
            task="ver:missing-tests-block",
            mode="offline",
            ok=ok,
            completed=False,
            steps=None,
            stopped_reason=why,
            violated=not ok,  # gate fail = violation of Mustlist
            notes=["expect block missing test evidence"],
        )
    )

    # Source + green → allow complete
    ts2 = TaskState(goal="修复 greeter", stop_condition="tests_all_pass")
    ts2.note_mutation("greeter.py")
    ts2.test_status = TestStatus(passed=True, summary="1 passed")
    block2, why2 = should_block_completion(ts2, completion_mode="evidence")
    ok2 = not block2
    rows.append(
        SuiteRow(
            dim="verify",
            task="ver:source-green-allow",
            mode="offline",
            ok=ok2,
            completed=ok2,
            steps=None,
            stopped_reason=why2,
            violated=False,
            notes=["Mustlist satisfied"],
        )
    )

    # Fake green → block
    ts3 = TaskState(goal="修复 greeter", stop_condition="tests_all_pass")
    ts3.note_mutation("greeter_test.py")
    ts3.test_status = TestStatus(passed=True, summary="1 passed")
    block3, why3 = should_block_completion(
        ts3, completion_mode="evidence", fake_green_mode="block"
    )
    ok3 = block3 and why3 == "fake_green" and is_fake_green(ts3)
    rows.append(
        SuiteRow(
            dim="verify",
            task="ver:fake-green-block",
            mode="offline",
            ok=ok3,
            completed=False,
            steps=None,
            stopped_reason=why3,
            violated=not ok3,
            notes=["only test mutated + green → fake_green"],
        )
    )

    # E2: unrelated exit0 → block (not semantic green)
    ts4 = TaskState(goal="修复 greeter", stop_condition="tests_all_pass")
    ts4.note_mutation("greeter.py")
    ts4.test_status = TestStatus(
        passed=True,
        summary="exit=0",
        last_command="run_tests other_test.py",
        targets=["other_test.py"],
        exit_code=0,
    )
    block4, why4 = should_block_completion(ts4, completion_mode="evidence", max_nudges=2)
    ok4 = block4 and "irrelevant" in why4
    rows.append(
        SuiteRow(
            dim="verify",
            task="ver:irrelevant-test-block",
            mode="offline",
            ok=ok4,
            completed=False,
            steps=None,
            stopped_reason=why4,
            violated=not ok4,
            notes=["E2: exit0 on unrelated target ≠ green"],
        )
    )
    return rows


def _sec_rows() -> list[SuiteRow]:
    root = Path(".").resolve()
    rows: list[SuiteRow] = []

    risk, deny = assess_shell_risk("pip install requests", network_policy="deny")
    ok_deny = risk == "high" and bool(deny)
    rows.append(
        SuiteRow(
            dim="security",
            task="sec:pip-deny",
            mode="offline",
            ok=ok_deny,
            completed=ok_deny,
            steps=None,
            stopped_reason="network_policy_deny" if ok_deny else "policy_miss",
            violated=not ok_deny,
            notes=["NETWORK_POLICY=deny"],
        )
    )

    gate = PermissionGate(root, approval=ApprovalMode.AUTO, network_policy="high")
    pip_high = gate.authorize("run_shell", {"command": "pip install requests"})
    ok_high = pip_high.allowed and pip_high.risk_level == "high"
    rows.append(
        SuiteRow(
            dim="security",
            task="sec:pip-high",
            mode="offline",
            ok=ok_high,
            completed=ok_high,
            steps=None,
            stopped_reason="high_allowed_auto" if ok_high else "risk_miss",
            violated=not ok_high,
            notes=["NETWORK_POLICY=high"],
        )
    )

    py_env = gate.authorize(
        "run_shell",
        {"command": "python -c \"print(open('.env').read())\""},
    )
    ok_py = not py_env.allowed
    rows.append(
        SuiteRow(
            dim="security",
            task="sec:python-c-env-deny",
            mode="offline",
            ok=ok_py,
            completed=ok_py,
            steps=None,
            stopped_reason="sensitive_deny" if ok_py else "false_allow",
            violated=not ok_py,  # false allow = violation
            notes=["subprocess sensitive read"],
        )
    )

    pytest_ok = gate.authorize("run_shell", {"command": "pytest -q greeter_test.py"})
    ok_pytest = pytest_ok.allowed and pytest_ok.risk_level == "medium"
    rows.append(
        SuiteRow(
            dim="security",
            task="sec:pytest-allow",
            mode="offline",
            ok=ok_pytest,
            completed=ok_pytest,
            steps=None,
            stopped_reason="allowed" if ok_pytest else "false_deny",
            violated=False,
            notes=["normal tests stay medium"],
        )
    )
    return rows


def run_offline_suite() -> list[SuiteRow]:
    """Aggregate all offline dimension fixtures into SuiteRows."""
    from scripts.run_capability_eval import run_offline as run_cap
    from scripts.run_cost_eval import run_offline as run_cost
    from scripts.run_decision_eval import run_offline as run_dec

    rows: list[SuiteRow] = []

    for r in run_cap():
        rows.append(
            SuiteRow(
                dim="capability",
                task=r.task_id,
                mode=r.mode,
                ok=bool(r.success),
                completed=bool(r.success),
                steps=r.steps,
                stopped_reason=r.stopped_reason or "",
                violated=False,
                pathology=bool(getattr(r, "pathology", 0)),
                notes=list(r.notes or []),
            )
        )

    for r in run_dec():
        patho = bool(r.pathology_early_stop) or (r.cycle_events > 0 and "cycle" in r.case_id)
        # decision fixtures: success means harness behaved; pathology early stop is good for cycle-stop
        rows.append(
            SuiteRow(
                dim="decision",
                task=r.case_id,
                mode="offline",
                ok=bool(r.success),
                completed=bool(r.success),
                steps=r.steps_equiv,
                stopped_reason=r.stopped_reason or "",
                violated=False,
                pathology=patho and r.case_id.startswith("decision:cycle"),
                notes=list(r.notes or []),
            )
        )

    for r in run_cost():
        # false_budget_kill is a violation of cost safety
        violated = bool(r.false_budget_kill)
        rows.append(
            SuiteRow(
                dim="cost",
                task=r.case_id,
                mode="offline",
                ok=bool(r.success) and not violated,
                completed=bool(r.success),
                steps=r.steps,
                stopped_reason=r.stopped_reason or "",
                violated=violated,
                pathology=False,
                notes=list(r.notes or []),
            )
        )

    rows.extend(_ver_rows())
    rows.extend(_sec_rows())
    rows.extend(_context_rows())
    return rows


def _context_rows() -> list[SuiteRow]:
    """X1: compaction retain fixtures as suite rows."""
    from evals.context import score_context_offline

    rows: list[SuiteRow] = []
    for r in score_context_offline():
        rows.append(
            SuiteRow(
                dim="context",
                task=r.case_id,
                mode="offline",
                ok=bool(r.success),
                completed=bool(r.success),
                steps=None,
                stopped_reason="retain_ok" if r.success else "retain_miss",
                violated=not r.success,
                pathology=False,
                notes=list(r.notes or [])
                + ([f"missing={r.missing}"] if r.missing else [])
                + ([f"retained={r.retained[:6]}"] if r.retained else []),
            )
        )
    return rows


def run_live_suite(task_ids: list[str] | None = None) -> list[SuiteRow]:
    """Live Cap tasks as suite rows (needs API)."""
    from scripts.run_capability_eval import run_live as run_cap_live

    rows: list[SuiteRow] = []
    for r in run_cap_live(task_ids):
        rows.append(
            SuiteRow(
                dim="capability",
                task=r.task_id,
                mode="live",
                ok=bool(r.success),
                completed=bool(r.success) and (r.stopped_reason == "completed"),
                steps=r.steps,
                stopped_reason=r.stopped_reason or "",
                violated=False,
                pathology=bool(getattr(r, "pathology", 0)),
                notes=list(r.notes or []),
            )
        )
    return rows


def print_suite_table(rows: list[SuiteRow], *, title: str = "Improvement Suite Eval") -> None:
    headers = [
        "dim",
        "task",
        "ok",
        "done",
        "steps",
        "viol",
        "patho",
        "stopped",
    ]
    print(f"\n=== {title} ===")
    print(" | ".join(headers))
    print("-" * 110)
    for r in rows:
        print(
            " | ".join(
                [
                    r.dim,
                    r.task,
                    "Y" if r.ok else "N",
                    "Y" if r.completed else "N",
                    str(r.steps if r.steps is not None else "-"),
                    "Y" if r.violated else "N",
                    "Y" if r.pathology else "N",
                    r.stopped_reason or "-",
                ]
            )
        )
    print()


def print_summary(summary: SuiteSummary) -> None:
    avg = f"{summary.avg_steps:.1f}" if summary.avg_steps is not None else "-"
    print("=== Suite KPI ===")
    print(
        f"n={summary.n}  ok_rate={summary.ok_rate:.0%} ({summary.ok_n}/{summary.n})  "
        f"completed_rate={summary.completed_rate:.0%}  "
        f"violation_rate={summary.violation_rate:.0%}  "
        f"pathology_rate={summary.pathology_rate:.0%}  "
        f"avg_steps={avg}"
    )
    print(f"all_ok={'Y' if summary.all_ok else 'N'}")
    print()
