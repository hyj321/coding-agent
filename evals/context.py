"""Context-dimension offline scoring (X1 — compaction retain).

抽检：observation 压缩 / MicroCompact / history fold 之后，关键路径与断言信号是否仍在
可被模型读到的 prompt 拼贴中（Current State / Root State / 压缩后的 tool 文本）。
"""

from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.agent.compress import compress_tool_result, microcompact_messages
from src.agent.context_manager import ContextManager


@dataclass
class ContextReport:
    """One offline context-compaction fixture row."""

    case_id: str
    success: bool
    retained: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    compress_events: int = 0
    fold_events: int = 0
    microcompact_events: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _must_retain(blob: str, tokens: list[str]) -> tuple[list[str], list[str]]:
    retained = [t for t in tokens if t in blob]
    missing = [t for t in tokens if t not in blob]
    return retained, missing


def _pytest_failure_log(*, fat: bool = True) -> str:
    noise = (("x" * 180 + "\n") * 50) if fat else ("noise line\n" * 5)
    return (
        "============================= test session starts ==============================\n"
        + noise
        + "FAILED greeter_test.py::test_hello - AssertionError: assert 'Hi' == 'Hello'\n"
        + "E       AssertionError: assert 'Hi' == 'Hello'\n"
        + "=========================== 1 failed, 0 passed in 0.08s ===========================\n"
        + "exit_code: 1\n"
    )


def _run_obs_pytest_retain() -> ContextReport:
    """Observation compress keeps path / assert / exit (not only shorter text)."""
    raw = _pytest_failure_log(fat=True)
    must = [
        "greeter_test.py",
        "AssertionError",
        "exit_code: 1",
        "1 failed",
    ]
    compact = compress_tool_result("run_shell", raw, soft_limit=1200, hard_limit=2400)
    retained, missing = _must_retain(compact, must)
    ok = not missing and len(compact) < len(raw) // 2
    return ContextReport(
        case_id="context:obs-pytest-retain",
        success=ok,
        retained=retained,
        missing=missing,
        compress_events=1 if compact != raw else 0,
        notes=[
            f"raw_chars={len(raw)}",
            f"compact_chars={len(compact)}",
            "expect failure card retains path+assert+exit",
        ],
    )


def _run_obs_state_paths() -> ContextReport:
    """observe_tool writes focus/errors; Current State still names critical paths."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cm = ContextManager(
            workdir=root,
            tool_names=["read_file", "edit_file", "run_tests", "run_shell"],
            token_budget=8000,
        )
        fail = _pytest_failure_log(fat=True)
        stored = cm.observe_tool(
            step=1,
            tool_name="run_tests",
            raw_args={"target": "greeter_test.py"},
            result=fail,
        )
        cm.observe_tool(
            step=2,
            tool_name="edit_file",
            raw_args={
                "path": "greeter.py",
                "old_string": "Hi",
                "new_string": "Hello",
            },
            result="Edited greeter.py: replaced 1 occurrence(s).",
        )
        state_blob = "\n".join(
            [
                cm.state.render_current_state(),
                cm.task_state.render_block(),
                stored,
            ]
        )
        must = ["greeter.py", "greeter_test.py"]
        # Error latch should keep a failure hint
        soft_must_any = ["AssertionError", "FAILED", "greeter_test"]
        retained, missing = _must_retain(state_blob, must)
        soft_ok = any(t in state_blob for t in soft_must_any)
        ok = not missing and soft_ok and cm.state.compress_events >= 1
        if not soft_ok:
            missing = list(missing) + ["<failure-signal>"]
        return ContextReport(
            case_id="context:obs-state-paths",
            success=ok,
            retained=retained + [t for t in soft_must_any if t in state_blob],
            missing=missing,
            compress_events=cm.state.compress_events,
            notes=["Current State / Task State retain focus + failure signal"],
        )


def _run_fold_retain() -> ContextReport:
    """After budget fold, Root/Current State still exposes critical paths + assert."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cm = ContextManager(
            workdir=root,
            tool_names=["read_file", "edit_file", "run_tests", "run_shell", "todo_write"],
            token_budget=1800,
            recent_keep_messages=6,
            observation_soft_chars=800,
            observation_hard_chars=1200,
        )
        fail = _pytest_failure_log(fat=True)
        cm.observe_tool(
            step=1,
            tool_name="run_tests",
            raw_args={"target": "greeter_test.py"},
            result=fail,
        )
        cm.observe_tool(
            step=2,
            tool_name="edit_file",
            raw_args={
                "path": "greeter.py",
                "old_string": "a",
                "new_string": "b",
            },
            result="Edited greeter.py: replaced 1 occurrence(s).",
        )
        cm.state.todos_text = (
            "Todo list:\n  [x] (1) locate\n  [ ] (2) fix greeter\n"
            "Progress: 1/2 completed"
        )

        msgs: list[dict[str, Any]] = [
            {"role": "system", "content": "system rules"},
            {"role": "user", "content": "修复 greeter 使 greeter_test.py 通过"},
        ]
        # Inflate history so prepare_messages must fold
        pad = ("PAD " * 80) + "\n"
        for i in range(14):
            msgs.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"c{i}",
                            "type": "function",
                            "function": {
                                "name": "run_shell",
                                "arguments": '{"command":"pytest -q"}',
                            },
                        }
                    ],
                }
            )
            # Early tool rows carry the failure; later ones are bulky noise
            body = fail if i < 2 else (pad * 8 + f"exit_code: 0\nstep={i}\n")
            msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": f"c{i}",
                    "content": body,
                }
            )

        prepared = cm.prepare_messages(
            msgs, user_task="修复 greeter 使 greeter_test.py 通过"
        )
        blob = "\n".join(str(m.get("content") or "") for m in prepared)
        # Also check live state renders (authoritative after fold)
        blob += "\n" + cm.state.render_current_state()
        blob += "\n" + cm.task_state.render_block()

        must = ["greeter.py", "greeter_test.py"]
        soft_any = ["AssertionError", "FAILED", "1 failed", "exit_code: 1"]
        retained, missing = _must_retain(blob, must)
        soft_ok = any(t in blob for t in soft_any)
        folded = cm.state.fold_events >= 1 or "Context folded" in blob or "Root State" in blob
        if not soft_ok:
            missing = list(missing) + ["<failure-signal>"]
        ok = not missing and soft_ok and folded
        return ContextReport(
            case_id="context:fold-retain",
            success=ok,
            retained=retained + [t for t in soft_any if t in blob],
            missing=missing,
            compress_events=cm.state.compress_events,
            fold_events=cm.state.fold_events,
            microcompact_events=cm.state.microcompact_events,
            notes=[
                f"folded={folded}",
                f"msgs_after={len(prepared)}",
                "expect path+assert survive fold via state reinject",
            ],
        )


def _run_microcompact_recent() -> ContextReport:
    """MicroCompact stubs old bulky tools but keeps recent failure card readable."""
    fail = compress_tool_result("run_tests", _pytest_failure_log(fat=True))
    msgs: list[dict[str, Any]] = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "fix greeter"},
    ]
    for i in range(6):
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"m{i}",
                        "type": "function",
                        "function": {"name": "run_tests", "arguments": "{}"},
                    }
                ],
            }
        )
        # Older: bulky success noise; newest: failure card
        if i < 5:
            content = ("ok line\n" * 80) + "exit_code: 0\n"
        else:
            content = fail
        msgs.append({"role": "tool", "tool_call_id": f"m{i}", "content": content})

    compacted, n_stubs = microcompact_messages(
        msgs, keep_recent_tools=2, stub_limit=180, min_chars_to_stub=200
    )
    blob = "\n".join(
        str(m.get("content") or "")
        for m in compacted
        if m.get("role") == "tool"
    )
    must = ["greeter_test.py", "AssertionError"]
    retained, missing = _must_retain(blob, must)
    ok = not missing and n_stubs >= 1
    return ContextReport(
        case_id="context:microcompact-recent",
        success=ok,
        retained=retained,
        missing=missing,
        microcompact_events=n_stubs,
        notes=[f"stubs={n_stubs}", "recent failure not stubbed away"],
    )


def score_context_offline() -> list[ContextReport]:
    return [
        _run_obs_pytest_retain(),
        _run_obs_state_paths(),
        _run_fold_retain(),
        _run_microcompact_recent(),
    ]
