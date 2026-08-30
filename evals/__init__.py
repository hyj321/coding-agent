"""Capability + Decision + Cost + Context + Improvement suite eval package."""

from __future__ import annotations

from evals.context import ContextReport, score_context_offline
from evals.cost import CostReport, score_cost_offline
from evals.decision import DecisionReport, count_pathology_from_stopped, score_decision_offline
from evals.score import EvalScore, extract_tool_trace, score_trace
from evals.suite import SuiteRow, SuiteSummary, run_offline_suite, summarize
from evals.tasks import TASKS, CapabilityTask, get_task

__all__ = [
    "TASKS",
    "CapabilityTask",
    "ContextReport",
    "CostReport",
    "DecisionReport",
    "EvalScore",
    "SuiteRow",
    "SuiteSummary",
    "count_pathology_from_stopped",
    "extract_tool_trace",
    "get_task",
    "run_offline_suite",
    "score_context_offline",
    "score_cost_offline",
    "score_decision_offline",
    "score_trace",
    "summarize",
]
