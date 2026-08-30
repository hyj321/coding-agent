"""Offline smoke tests for Day2 harness pieces (no API key required).

Run: python -m scripts.smoke_v1
"""

from __future__ import annotations

import json
import re
import tempfile
import threading
import time
from pathlib import Path

from src.agent.context import trim_messages
from src.agent.loop import AgentResult
from src.agent.permissions import ApprovalMode, PermissionGate, SandboxError
from src.agent.transcript import build_continue_context, load_session, save_transcript
from src.tools import build_default_registry


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "notes.txt").write_text("hello world", encoding="utf-8")
        (root / "sub").mkdir()
        (root / "sub" / "a.py").write_text("x = 1\n", encoding="utf-8")

        gate = PermissionGate(root, approval=ApprovalMode.AUTO)
        reg = build_default_registry(gate, max_output_chars=2000)

        expected = {
            "edit_file",
            "git_diff",
            "git_status",
            "glob",
            "grep",
            "list_dir",
            "load_skill",
            "memory_search",
            "rag_search",
            "read_file",
            "run_shell",
            "run_tests",
            "todo_write",
            "write_file",
        }
        assert set(reg.names()) == expected, reg.names()

        skill_body = reg.dispatch("load_skill", {"name": "debugging"})
        assert skill_body.startswith("# Skill: debugging"), skill_body[:80]
        assert "todo_write" in skill_body and "edit_file" in skill_body
        assert "Search-first" in skill_body and "grep" in skill_body
        assert reg.dispatch("load_skill", {"name": "missing"}).startswith("Error")
        for extra in ("testing", "refactoring"):
            body = reg.dispatch("load_skill", {"name": extra})
            assert body.startswith(f"# Skill: {extra}"), body[:80]
        testing_body = reg.dispatch("load_skill", {"name": "testing"})
        assert "Search-first" in testing_body and "glob" in testing_body

        from src.agent.context_manager import build_system_prompt
        from src.agent.skills import (
            discover_skills,
            format_skill_preload,
            format_skills_catalog,
            suggest_skills,
        )

        names = {s.name for s in discover_skills()}
        assert {"debugging", "testing", "refactoring"} <= names
        catalog = format_skills_catalog()
        assert "Available Skills" in catalog and "debugging" in catalog
        assert "testing" in catalog and "refactoring" in catalog
        prompt = build_system_prompt(root, reg.names())
        assert "Available Skills" in prompt and "load_skill" in prompt
        assert "Batch tools" in prompt or "Batch tools in ONE" in prompt
        assert "phase-level" in prompt or "phase boundaries" in prompt
        assert "Trivial one-shot" in prompt
        assert "Search-first" in prompt
        assert "BEFORE whole-tree list_dir" in prompt or "grep and/or glob" in prompt
        assert "Preloaded" in catalog or "preloaded" in catalog.lower()

        # Keyword router: bug fix → debugging; 补测 → testing; 重构 → refactoring
        dbg = suggest_skills("修复 greeter 测试失败的 bug")
        assert dbg and dbg[0].name == "debugging", dbg
        tst = suggest_skills("给 buggy_calc 补测并提高 coverage")
        assert tst and tst[0].name == "testing", tst
        ref = suggest_skills("重构 greet 函数，不改行为")
        assert ref and ref[0].name == "refactoring", ref
        assert suggest_skills("写一个 hello world") == []
        preload = format_skill_preload("debugging", score=5, matched=("bug", "修复"))
        assert "Preloaded Skill: debugging" in preload
        assert "Do **not** call" in preload or "load_skill" in preload

        from src.agent.cancel import build_interrupt_message, is_cancelled

        assert is_cancelled(None) is False
        ev = threading.Event()
        assert is_cancelled(ev) is False
        ev.set()
        assert is_cancelled(ev) is True
        msg = build_interrupt_message(changed_files=["greeter.py", "greeter.py", "a.py"])
        assert "已按你的要求停止" in msg
        assert "改成只修测试" in msg
        assert msg.count("greeter.py") == 1
        assert "`a.py`" in msg

        # Cooperative cancel: Event set before step 1 → interrupted without LLM
        class _FakeCfg:
            model = "fake"
            context_token_budget = 8000
            tool_visibility = "all"
            completion_mode = "off"
            evidence_nudge_max = 0
            loop_warn_after = 3
            loop_stop_after = 5
            loop_error_nudge_after = 2
            retry_max_failures = 3
            final_nudge_mutating_limit = 2
            workdir = root

        class _FakeClient:
            config = _FakeCfg()
            cache_policy = None

            def chat(self, *_a, **_k):
                raise AssertionError("LLM should not be called when cancel is already set")

        from src.agent.loop import run_agent as _run_agent

        cancel = threading.Event()
        cancel.set()
        events = []
        result = _run_agent(
            client=_FakeClient(),
            registry=reg,
            system_prompt="sys",
            user_task="do something long",
            max_steps=5,
            gate=gate,
            on_event=events.append,
            cancel_event=cancel,
            persist_memory_md=False,
        )
        assert result.stopped_reason == "interrupted"
        assert "已按你的要求停止" in result.final_text
        assert any(e.get("type") == "final" and e.get("stopped_reason") == "interrupted" for e in events)

        # Cost-A: TaskBudget unit + sync gate before LLM
        from types import SimpleNamespace

        from src.agent.task_budget import TaskBudget

        tb = TaskBudget(max_task_tokens=1000, output_reserve=100)
        assert tb.enabled
        assert not tb.would_exceed(100)
        assert tb.would_exceed(950)  # 0+950+100 > 1000
        tb.record_llm_turn(prompt_tokens=400, completion_tokens=50)
        assert tb.tokens_used == 450
        assert tb.llm_calls == 1
        deny = tb.check_before_llm(
            [{"role": "user", "content": "x" * 2000}]  # ~500 tok + reserve
        )
        assert deny is not None and deny["budget_kind"] == "tokens"
        assert TaskBudget(max_task_tokens=0).check_before_llm(
            [{"role": "user", "content": "huge " * 5000}]
        ) is None

        class _BudgetCfg(_FakeCfg):
            max_task_tokens = 80
            task_token_output_reserve = 20
            tool_visibility = "off"
            completion_mode = "trust_model"

        class _CountingClient:
            def __init__(self) -> None:
                self.calls = 0
                self.config = _BudgetCfg()
                self.cache_policy = None

            def chat(self, *_a, **_k):
                self.calls += 1
                raise AssertionError("LLM must not run when first prompt exceeds budget")

        budget_client = _CountingClient()
        budget_events: list = []
        budget_result = _run_agent(
            client=budget_client,
            registry=reg,
            system_prompt="sys",
            user_task="do a long coding task that needs tools",
            max_steps=5,
            gate=gate,
            on_event=budget_events.append,
            persist_memory_md=False,
            max_task_tokens=80,
        )
        assert budget_result.stopped_reason == "budget_exhausted"
        assert budget_client.calls == 0
        assert budget_result.steps == 0
        assert budget_result.memory and budget_result.memory.get("task_budget", {}).get(
            "budget_kind"
        ) == "tokens"
        cr0 = budget_result.memory.get("cost_report") or {}
        assert cr0.get("tokens_total_est") == 0
        assert "tool_counts" in cr0
        assert any(e.get("type") == "budget_exhausted" for e in budget_events)
        assert any(e.get("type") == "cost_report" for e in budget_events)
        assert any(
            e.get("type") == "final" and e.get("stopped_reason") == "budget_exhausted"
            for e in budget_events
        )

        # Cost-B: Current State budget line + warn + cost_report tools
        from src.agent.context_manager import ContextState

        st = ContextState(budget_line="Budget: steps 2/10 | tokens≈1k (cap=off) | level=ok")
        rendered = st.render_current_state()
        assert "Budget: steps 2/10" in rendered
        warn_tb = TaskBudget(max_task_tokens=1000, output_reserve=50, warn_ratio=0.2)
        warn_tb.record_llm_turn(prompt_tokens=700, completion_tokens=100)
        assert warn_tb.remaining_pct() is not None and warn_tb.remaining_pct() <= 20
        w1 = warn_tb.maybe_warn_message(step=2, max_steps=10)
        assert w1 and "[budget_warn]" in w1
        assert warn_tb.maybe_warn_message(step=3, max_steps=10) is None  # one-shot
        line = warn_tb.format_line(step=2, max_steps=10)
        assert "level=warn" in line or "level=critical" in line
        assert "Budget:" in line

        # Cost-A: accumulate then block second LLM call (no ContextManager → tiny prompts)
        class _TwoStepClient:
            def __init__(self) -> None:
                self.calls = 0
                self.config = SimpleNamespace(
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
                self.cache_policy = None

            def chat(self, messages, tools=None):
                self.calls += 1
                if self.calls == 1:
                    tc = SimpleNamespace(
                        id="call_budget_1",
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
                    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)
                raise AssertionError("second LLM call should be blocked by task budget")

        two = _TwoStepClient()
        two_events: list = []
        two_result = _run_agent(
            client=two,
            registry=reg,
            system_prompt="sys",
            user_task="list then stop",
            max_steps=5,
            gate=None,
            on_event=two_events.append,
            persist_memory_md=False,
            max_task_tokens=80,
            context_manager=None,
            max_messages=20,
        )
        assert two.calls == 1, f"expected 1 LLM call, got {two.calls}"
        assert two_result.stopped_reason == "budget_exhausted"
        assert two_result.steps == 1
        assert two_result.memory["task_budget"]["llm_calls"] == 1
        cr = two_result.memory.get("cost_report") or {}
        assert cr.get("llm_calls") == 1
        assert cr.get("tool_counts", {}).get("list_dir") == 1
        assert cr.get("tokens_total_est", 0) > 0
        assert "summary" in cr and "list_dir" in cr["summary"]
        assert any(e.get("type") == "budget_exhausted" for e in two_events)
        assert any(e.get("type") == "cost_report" and e.get("tool_counts") for e in two_events)
        # Mid-run steer inbox drains into messages
        from src.agent.steer import SteerInbox, format_steer_message, STEER_MARKER

        inbox = SteerInbox()
        assert inbox.push("  only fix tests  ")
        assert inbox.pending_count() == 1
        assert inbox.drain() == ["only fix tests"]
        assert inbox.drain() == []
        assert STEER_MARKER in format_steer_message("改成只修测试")

        from src.agent.loop import LoopGuard, tool_call_fingerprint
        from src.agent.loop_guard import LoopGuard as LoopGuardDirect
        from src.agent.retry_policy import RetryPolicy, make_failure_key
        from src.agent.stop_conditions import (
            build_final_nudge_message,
            clear_nudge_state,
            evaluate_final_nudge,
            reasons_allow_force_stop,
            should_force_stop_after_nudge,
            todos_all_completed,
        )
        from src.agent.task_state import TaskState, parse_test_status

        # Fingerprint: key order / whitespace must not create false uniqueness
        fp_a = tool_call_fingerprint("read_file", {"path": "a.py"})
        fp_b = tool_call_fingerprint("read_file", {"path": " a.py "})
        fp_c = tool_call_fingerprint("read_file", '{"path":"a.py"}')
        assert fp_a == fp_b == fp_c
        assert tool_call_fingerprint("read_file", {"path": "b.py"}) != fp_a
        assert LoopGuard is LoopGuardDirect

        guard = LoopGuard(warn_after=3, stop_after=5, error_nudge_after=2)
        streaks = []
        for _ in range(5):
            s, _ = guard.observe("read_file", {"path": "a.py"})
            streaks.append(s)
        assert streaks == [1, 2, 3, 4, 5]
        assert guard.warning_suffix("read_file", 3)
        assert "STOP" in (guard.warning_suffix("read_file", 5) or "")
        s_reset, _ = guard.observe("read_file", {"path": "b.py"})
        assert s_reset == 1

        # Same-step dedup
        guard2 = LoopGuard.from_env(warn_after=3, stop_after=5, error_nudge_after=2)
        guard2.begin_step()
        _, fp_d = guard2.observe("read_file", {"path": "notes.txt"})
        assert guard2.same_step_lookup(fp_d) is None
        guard2.same_step_store(fp_d, "hello world")
        cached = guard2.same_step_lookup(fp_d)
        assert cached == "hello world"
        reuse_msg = LoopGuard.dedup_reuse_message("read_file", cached)
        assert reuse_msg.startswith("[dedup]") and "hello world" in reuse_msg
        guard2.begin_step()
        assert guard2.same_step_lookup(fp_d) is None

        # Error-streak nudge
        guard3 = LoopGuard(warn_after=3, stop_after=5, error_nudge_after=2)
        _, fp_e = guard3.observe("edit_file", {"path": "x.py", "old_string": "a", "new_string": "b"})
        assert guard3.record_outcome(fp_e, ok=False) == 1
        assert guard3.error_nudge_suffix("edit_file", 1) is None
        assert guard3.record_outcome(fp_e, ok=False) == 2
        nudge = guard3.error_nudge_suffix("edit_file", 2)
        assert nudge and "ERROR_STREAK" in nudge
        assert guard3.record_outcome(fp_e, ok=True) == 0

        # RetryPolicy stage 1 → 2 → 3 ban (hard BLOCK on next same fingerprint)
        rp = RetryPolicy(max_failures=3)
        args_fail = {"path": "greeter.py", "old_string": "a", "new_string": "b"}
        err_body = "Error: old_string not found\nAssertionError: boom"
        key = make_failure_key("edit_file", args_fail, err_body)
        assert "edit_file" in key and "greeter.py" in key
        d1 = rp.record_failure(tool_name="edit_file", args=args_fail, result=err_body)
        assert d1.stage == 1 and not d1.should_stop and "stage=1" in (d1.suffix or "")
        d2 = rp.record_failure(tool_name="edit_file", args=args_fail, result=err_body)
        assert d2.stage == 2 and not d2.should_stop and "MUST change" in (d2.suffix or "")
        d3 = rp.record_failure(tool_name="edit_file", args=args_fail, result=err_body)
        assert d3.stage == 3 and d3.should_stop and "BLOCKED" in (d3.suffix or "")
        assert "edit_file|" in rp.banned_strategies_text()
        assert "Banned strategies" in (d3.suffix or "")
        fp_fail = tool_call_fingerprint("edit_file", args_fail)
        assert not rp.is_blocked(fp_fail)
        rp.ban_fingerprint(fp_fail)
        assert rp.is_blocked(fp_fail)
        block_msg = rp.blocked_tool_message("edit_file")
        assert block_msg.startswith("Error: BLOCKED") and "change args" in block_msg
        assert rp.block_hits == 1
        assert rp.to_dict()["failed"]
        assert fp_fail in rp.to_dict()["blocked_fingerprints"]
        rp2 = RetryPolicy.from_dict(rp.to_dict())
        assert rp2.is_blocked(fp_fail)

        # Cycle detection: A↔B ping-pong (warn at 2 repeats, stop at 3)
        guard_c = LoopGuard(
            warn_after=3,
            stop_after=5,
            error_nudge_after=2,
            cycle_warn_repeats=2,
            cycle_stop_repeats=3,
            cycle_max_period=4,
        )
        seq_ab = [
            ("read_file", {"path": "a.py"}),
            ("run_tests", {"target": "t.py"}),
        ]
        # 4 calls = A B A B → warn (repeats=2)
        for i in range(4):
            name_i, args_i = seq_ab[i % 2]
            guard_c.observe(name_i, args_i)
        hit_w = guard_c.cycle_status()
        assert hit_w is not None and hit_w.level == "warn" and hit_w.period == 2
        assert hit_w.repeats == 2
        assert "CYCLE_WARN" in (guard_c.cycle_suffix() or "")
        # 6 calls = A B A B A B → stop (repeats=3)
        for i in range(4, 6):
            name_i, args_i = seq_ab[i % 2]
            guard_c.observe(name_i, args_i)
        hit_s = guard_c.cycle_status()
        assert hit_s is not None and hit_s.level == "stop" and hit_s.repeats == 3
        assert "CYCLE_STOP" in (guard_c.cycle_suffix() or "")
        # Exact streak must NOT count as cycle
        guard_e = LoopGuard(cycle_warn_repeats=2, cycle_stop_repeats=3)
        for _ in range(6):
            guard_e.observe("read_file", {"path": "same.py"})
        assert guard_e.cycle_status() is None
        assert guard_e.streak == 6

        # Observation stagnation (Dec-B): warn at 3 identical obs; stop disabled by default
        from src.agent.loop_guard import observation_fingerprint

        assert observation_fingerprint("read_file", "hello  world") == observation_fingerprint(
            "read_file", "hello world"
        )
        assert observation_fingerprint("read_file", "a") != observation_fingerprint(
            "run_tests", "a"
        )
        guard_s = LoopGuard(stagnation_warn_after=3, stagnation_stop_after=0)
        for i in range(3):
            n = guard_s.record_observation("read_file", "same body")
            assert n == i + 1
        stag = guard_s.stagnation_suffix("read_file")
        assert stag and "STAGNATION_WARN" in stag and "STAGNATION_STOP" not in stag
        guard_s2 = LoopGuard(stagnation_warn_after=3, stagnation_stop_after=5)
        for _ in range(5):
            guard_s2.record_observation("run_tests", "FAILED exit_code: 1")
        assert "STAGNATION_STOP" in (guard_s2.stagnation_suffix("run_tests") or "")
        guard_s3 = LoopGuard(stagnation_warn_after=3, stagnation_stop_after=0)
        guard_s3.record_observation("read_file", "a")
        guard_s3.record_observation("read_file", "b")
        assert guard_s3.obs_streak == 1
        assert guard_s3.stagnation_suffix("read_file") is None

        # Decision discipline present in system prompt
        from src.agent.context_manager import ContextManager

        with tempfile.TemporaryDirectory() as td:
            cm_dec = ContextManager(workdir=Path(td), tool_names=["read_file", "run_tests"])
            sp = cm_dec.build_system_prompt()
            assert "Decision discipline" in sp
            assert "D1." in sp and "D2." in sp and "D3." in sp
            assert "BLOCKED" in sp
            assert "Plan-then-Act" in sp or "plan-then-act" in sp.lower() or "todo_write" in sp

        # TaskState + pytest parse + stop conditions
        ts = TaskState.from_goal("fix greeter tests")
        assert "greeter" in ts.goal
        ts.note_file("greeter.py")
        ts.note_error("AssertionError: boom")
        fail_out = (
            "======= FAILURES =======\n"
            "FAILED greeter_test.py::test_x\n"
            "1 failed, 0 passed\n"
            "exit_code: 1\n"
        )
        parsed_fail = parse_test_status("python -m pytest greeter_test.py -q", fail_out)
        assert parsed_fail is not None and parsed_fail.passed is False
        pass_out = "2 passed in 0.1s\nexit_code: 0\n"
        parsed_pass = parse_test_status("pytest greeter_test.py", pass_out)
        assert parsed_pass is not None and parsed_pass.passed is True
        ts.test_status = parsed_pass
        block = ts.render_block()
        assert "Task State" in block and "PASSED" in block and "greeter.py" in block

        todos_done = "Todo list:\n  [x] (1) locate\n  [x] (2) fix\n"
        assert todos_all_completed(todos_done)
        assert not todos_all_completed("Todo list:\n  [>] (1) locate\n")
        urge, reasons = evaluate_final_nudge(
            task_state=ts, todos_text=todos_done, step_had_failure=False
        )
        assert urge and "tests_all_pass" in reasons
        assert reasons_allow_force_stop(reasons)
        # todo-only must NOT force-stop (UI polish / continue-edit UX)
        ts_todo = TaskState.from_goal("polish UI")
        urge_todo, reasons_todo = evaluate_final_nudge(
            task_state=ts_todo, todos_text=todos_done, step_had_failure=False
        )
        assert urge_todo and reasons_todo == ["todo_all_done"]
        assert not reasons_allow_force_stop(reasons_todo)
        assert not should_force_stop_after_nudge(
            mutating_count=99, limit=2, reasons=reasons_todo
        )
        assert should_force_stop_after_nudge(
            mutating_count=2, limit=2, reasons=["tests_all_pass"]
        )
        nudge_text = build_final_nudge_message(reasons, task_state=ts)
        assert nudge_text.startswith("[stop_condition]") and "FINAL" in nudge_text
        soft = build_final_nudge_message(["todo_all_done"], task_state=ts_todo)
        assert "可以继续编辑" in soft
        clear_nudge_state(ts)
        assert ts.final_nudge_sent is False and ts.stop_nudge_reasons == []

        from src.agent.context_manager import ContextManager

        cm = ContextManager(workdir=root, tool_names=reg.names(), token_budget=8000)
        cm.ensure_task_goal("fix notes.txt")
        compressed = cm.observe_tool(
            step=1,
            tool_name="read_file",
            raw_args={"path": "notes.txt"},
            result="hello world",
        )
        assert compressed == "hello world"
        assert "notes.txt" in cm.task_state.relevant_files
        # Simulate staged failures via retry_policy on context
        cm.retry_policy = RetryPolicy(max_failures=3)
        cm.retry_policy.record_failure(
            tool_name="edit_file",
            args={"path": "notes.txt", "old_string": "x", "new_string": "y"},
            result="Error: not found",
        )
        cm._sync_task_retry_fields()
        note = cm._state_note()
        assert "Task State" in note and "fix notes.txt" in note
        assert "Failed strategies" in note or "retry" in note.lower() or cm.task_state.failed
        mem = cm.export_memory()
        assert isinstance(mem.get("task_state"), dict)
        assert mem["task_state"]["goal"]
        assert isinstance(mem.get("retry_policy"), dict)

        todo_desc = next(
            s["function"]["description"]
            for s in reg.openai_tools()
            if s["function"]["name"] == "todo_write"
        )
        assert "phase" in todo_desc.lower() or "3–5" in todo_desc or "3-5" in todo_desc
        assert "SAME" in todo_desc or "same" in todo_desc.lower()

        listed = reg.dispatch("list_dir", {"path": "."})
        assert "notes.txt" in listed and "sub" in listed

        content = reg.dispatch("read_file", {"path": "notes.txt"})
        assert content == "hello world"

        edited = reg.dispatch(
            "edit_file",
            {
                "path": "notes.txt",
                "old_string": "world",
                "new_string": "agent",
            },
        )
        assert "replaced 1" in edited
        assert (root / "notes.txt").read_text(encoding="utf-8") == "hello agent"

        missing = reg.dispatch(
            "edit_file",
            {"path": "notes.txt", "old_string": "nope", "new_string": "x"},
        )
        assert missing.startswith("Error")

        gl = reg.dispatch("glob", {"pattern": "**/*.py"})
        assert "sub/a.py" in gl.replace("\\", "/")

        # read_file offset/limit (1-based lines)
        (root / "long.py").write_text(
            "\n".join(f"line{i}" for i in range(1, 11)) + "\n",
            encoding="utf-8",
        )
        sliced = reg.dispatch("read_file", {"path": "long.py", "offset": 3, "limit": 2})
        assert "lines 3-4 of 10" in sliced
        assert "line3" in sliced and "line4" in sliced
        assert "line2" not in sliced and "line5" not in sliced
        past = reg.dispatch("read_file", {"path": "long.py", "offset": 99})
        assert past.startswith("Error")

        grepped = reg.dispatch("grep", {"pattern": r"line[79]", "path": "long.py"})
        assert "long.py:7:" in grepped and "long.py:9:" in grepped

        written = reg.dispatch(
            "write_file",
            {"path": "sub/out.py", "content": "print(42)\n"},
        )
        assert "out.py" in written

        grepped_dir = reg.dispatch("grep", {"pattern": r"print\(42\)", "path": "."})
        assert "sub/out.py" in grepped_dir.replace("\\", "/")
        bad_re = reg.dispatch("grep", {"pattern": "("})
        assert bad_re.startswith("Error")

        # Cap-A C5: Python syntax guardrail — reject bad edit/write, leave file intact
        good_py = (root / "sub" / "a.py").read_text(encoding="utf-8")
        bad_edit = reg.dispatch(
            "edit_file",
            {
                "path": "sub/a.py",
                "old_string": "x = 1\n",
                "new_string": "def broken(\n",
            },
        )
        assert bad_edit.startswith("Error: syntax rejected"), bad_edit
        assert "NOT modified" in bad_edit
        assert (root / "sub" / "a.py").read_text(encoding="utf-8") == good_py

        bad_write = reg.dispatch(
            "write_file",
            {"path": "sub/bad_new.py", "content": "def nope(\n"},
        )
        assert bad_write.startswith("Error: syntax rejected"), bad_write
        assert not (root / "sub" / "bad_new.py").exists()

        ok_write = reg.dispatch(
            "write_file",
            {"path": "sub/ok_new.py", "content": "y = 2\n"},
        )
        assert "ok_new.py" in ok_write
        assert (root / "sub" / "ok_new.py").read_text(encoding="utf-8") == "y = 2\n"

        # Cap-A C6: long file without offset → auto-head (not full body)
        big_lines = [f"row{i}\n" for i in range(1, 121)]
        (root / "big.py").write_text("".join(big_lines), encoding="utf-8")
        auto_head = reg.dispatch("read_file", {"path": "big.py"})
        assert "auto-head" in auto_head
        assert "lines 1-100 of 120" in auto_head
        assert "row1" in auto_head and "row100" in auto_head
        assert "row101" not in auto_head
        assert "offset=101" in auto_head
        continued = reg.dispatch(
            "read_file", {"path": "big.py", "offset": 101, "limit": 20}
        )
        assert "row101" in continued and "row120" in continued

        shell = reg.dispatch("run_shell", {"command": "python -c \"print('ok')\""})
        assert "exit_code: 0" in shell
        assert "ok" in shell

        # Cap-B C3: run_tests structured summary + TaskState
        (root / "mini_test.py").write_text(
            "def test_ok():\n    assert 1 + 1 == 2\n\n"
            "if __name__ == '__main__':\n"
            "    test_ok()\n"
            "    print('ok')\n",
            encoding="utf-8",
        )
        rt = reg.dispatch(
            "run_tests", {"target": "mini_test.py", "runner": "python"}
        )
        assert "# run_tests" in rt and "passed: true" in rt and "exit_code: 0" in rt
        from src.agent.task_state import TaskState as _TS

        ts_rt = _TS(goal="smoke")
        ts_rt.update_from_tool(
            tool_name="run_tests",
            args={"target": "mini_test.py", "runner": "python"},
            result=rt,
        )
        assert ts_rt.test_status is not None
        assert ts_rt.test_status.passed is True

        (root / "mini_fail_test.py").write_text(
            "def test_bad():\n    assert False\n\n"
            "if __name__ == '__main__':\n"
            "    test_bad()\n",
            encoding="utf-8",
        )
        rt_fail = reg.dispatch(
            "run_tests", {"target": "mini_fail_test.py", "runner": "python"}
        )
        assert "passed: false" in rt_fail
        assert re.search(r"(?m)^exit_code:\s*[1-9]", rt_fail)

        missing_t = reg.dispatch("run_tests", {"target": "no_such_test.py"})
        assert missing_t.startswith("Error")

        # Cap-B C2: git_status / git_diff (workdir may not be a git repo)
        gs = reg.dispatch("git_status", {})
        assert isinstance(gs, str) and len(gs) > 0
        # Either clean status or explicit not-a-repo error — both OK for smoke
        assert (
            gs.startswith("Error: workdir is not a git repository")
            or gs.startswith("Error: git executable not found")
            or not gs.startswith("Error:")
        )
        gd = reg.dispatch("git_diff", {})
        assert isinstance(gd, str)
        assert reg.get("run_tests").risk_level == "medium"
        assert reg.get("git_status").is_readonly is True
        assert reg.get("git_diff").risk_level == "low"

        # Cap-C: offline capability eval (metrics + plant-bug/run_tests path)
        from scripts.run_capability_eval import run_offline

        cap_rows = run_offline()
        assert cap_rows and all(r.success for r in cap_rows), cap_rows

        # Dec-C: offline decision eval (cycle / BLOCK / stagnation / no-false-cycle)
        from scripts.run_decision_eval import run_offline as run_decision_offline

        dec_rows = run_decision_offline()
        assert dec_rows and all(r.success for r in dec_rows), dec_rows
        assert any(r.case_id == "decision:cycle-stop" and r.pathology_early_stop for r in dec_rows)
        assert any(r.case_id == "decision:block" and r.blocked_replays >= 1 for r in dec_rows)

        # Cost-C: offline cost eval (budget stop / warn / no-false-kill)
        from scripts.run_cost_eval import run_offline as run_cost_offline

        cost_rows = run_cost_offline()
        assert cost_rows and all(r.success for r in cost_rows), cost_rows
        assert any(r.case_id == "cost:budget-stop-first" and r.budget_stop_early for r in cost_rows)
        assert any(
            r.case_id == "cost:gate-off-no-false-kill" and not r.false_budget_kill for r in cost_rows
        )

        # Windows GBK crash regression: UTF-8 bytes that are illegal as GBK
        from src.tools.shell import _decode_output

        bad_for_gbk = bytes([0xe2, 0x80, 0x94])  # UTF-8 em-dash
        assert "—" in _decode_output(bad_for_gbk) or "\ufffd" in _decode_output(
            bad_for_gbk
        ) or len(_decode_output(bad_for_gbk)) > 0
        mixed = reg.dispatch(
            "run_shell",
            {"command": "python -c \"import sys; sys.stdout.buffer.write(b'hi-\\xe2\\x80\\x94\\n')\""},
        )
        assert "exit_code: 0" in mixed
        assert "hi-" in mixed
        assert "UnicodeDecodeError" not in mixed

        escaped = reg.dispatch("read_file", {"path": "../outside.txt"})
        assert escaped.startswith("Error"), escaped

        try:
            gate.resolve_path("../outside.txt")
            raise AssertionError("expected SandboxError")
        except SandboxError:
            pass

        # Hard-deny shell
        denied = gate.authorize("run_shell", {"command": "rm -rf /"})
        assert not denied.allowed

        # never mode denies risky rm -rf of a folder
        never_gate = PermissionGate(root, approval=ApprovalMode.NEVER)
        never_gate.bind_registry(reg)
        risky = never_gate.authorize("run_shell", {"command": "rm -rf build"})
        assert not risky.allowed
        assert risky.risk_level == "high"

        # ask mode with auto-deny callback
        ask_gate = PermissionGate(
            root,
            approval=ApprovalMode.ASK,
            ask_fn=lambda _p: False,
        )
        ask_gate.bind_registry(reg)
        blocked = ask_gate.authorize("write_file", {"path": "x", "content": "y"})
        assert not blocked.allowed

        # Web ApprovalBridge: emit + resolve Allow
        from src.agent.permissions import ApprovalPrompt
        from src.web.approval import ApprovalBridge

        events: list[dict] = []
        bridge = ApprovalBridge(events.append, timeout_sec=5.0)
        result_box: list[bool] = []
        prompt = ApprovalPrompt(
            tool_name="write_file",
            risk_level="medium",
            summary="x.py",
            arguments={"path": "x.py"},
            call_id="call_1",
        )

        def _ask_worker() -> None:
            result_box.append(bridge.ask(prompt))

        ask_thread = threading.Thread(target=_ask_worker, daemon=True)
        ask_thread.start()
        for _ in range(50):
            if bridge.pending_id():
                break
            time.sleep(0.02)
        else:
            raise AssertionError("approval_request never became pending")
        rid = bridge.pending_id() or ""
        assert any(e.get("type") == "approval_request" for e in events)
        assert bridge.resolve(rid, True)["ok"]
        ask_thread.join(timeout=2)
        assert result_box == [True]
        assert any(e.get("type") == "approval_resolved" and e.get("allowed") for e in events)
        bridge.close()

        # Stop during pending approval → ask returns False quickly
        cancel_ev = threading.Event()
        cancel_events: list[dict] = []
        cancel_bridge = ApprovalBridge(
            cancel_events.append, timeout_sec=30.0, cancel_event=cancel_ev
        )
        cancel_box: list[bool] = []

        def _ask_then_cancel() -> None:
            cancel_box.append(
                cancel_bridge.ask(
                    ApprovalPrompt(
                        tool_name="run_shell",
                        risk_level="high",
                        summary="rm -rf",
                        arguments={"command": "echo x"},
                        call_id="call_stop",
                    )
                )
            )

        t_ask = threading.Thread(target=_ask_then_cancel, daemon=True)
        t_ask.start()
        for _ in range(50):
            if cancel_bridge.pending_id():
                break
            time.sleep(0.02)
        else:
            raise AssertionError("cancel approval never pending")
        cancel_ev.set()
        cancel_bridge.close()
        t_ask.join(timeout=2)
        assert cancel_box == [False]
        assert any(
            e.get("type") == "approval_resolved" and e.get("reason") == "cancelled"
            for e in cancel_events
        )

        # ask_min_risk=high: medium write auto-allowed without callback
        high_only = PermissionGate(
            root,
            approval=ApprovalMode.ASK,
            ask_fn=lambda _p: (_ for _ in ()).throw(AssertionError("should not ask")),
            ask_min_risk="high",
        )
        high_only.bind_registry(reg)
        auto_med = high_only.authorize("write_file", {"path": "x", "content": "y"})
        assert auto_med.allowed
        assert "ask_min_risk" in auto_med.reason

        # --- 17.8 P0: risk metadata + sensitive paths + shell risk ---
        from src.agent.permissions import is_sensitive_path

        assert is_sensitive_path(".env")
        assert is_sensitive_path(".env.local")
        assert is_sensitive_path("secrets/token.txt")
        assert is_sensitive_path(".ssh/id_rsa")
        assert is_sensitive_path("server.pem")
        assert not is_sensitive_path("greeter.py")

        # Tool metadata on registry
        assert reg.get("read_file").risk_level == "low" and reg.get("read_file").is_readonly
        assert reg.get("list_dir").risk_level == "low"
        assert reg.get("write_file").risk_level == "medium"
        assert reg.get("edit_file").risk_level == "medium"
        assert reg.get("run_shell").risk_level == "medium"
        assert reg.get("todo_write").risk_level == "low"

        # Sensitive path always Deny (even approval=auto)
        env_deny = gate.authorize("read_file", {"path": ".env"})
        assert not env_deny.allowed
        assert "sensitive" in env_deny.reason
        assert env_deny.risk_level == "high"
        env_write = gate.authorize("write_file", {"path": ".env", "content": "x=1"})
        assert not env_write.allowed
        cat_env = gate.authorize("run_shell", {"command": "cat .env"})
        assert not cat_env.allowed
        assert "sensitive" in cat_env.reason

        # High shell: git reset --hard → high; auto allows, never denies
        reset_auto = gate.authorize("run_shell", {"command": "git reset --hard"})
        assert reset_auto.allowed and reset_auto.risk_level == "high"
        reset_never = never_gate.authorize("run_shell", {"command": "git reset --hard"})
        assert not reset_never.allowed and reset_never.risk_level == "high"

        # Normal pytest stays medium and is Allowed under auto
        pytest_ok = gate.authorize("run_shell", {"command": "pytest -q greeter_test.py"})
        assert pytest_ok.allowed and pytest_ok.risk_level == "medium"

        # Low-risk tools always Allow (even ask/never)
        low_list = never_gate.authorize("list_dir", {"path": "."})
        assert low_list.allowed and low_list.risk_level == "low"
        low_read = ask_gate.authorize("read_file", {"path": "notes.txt"})
        assert low_read.allowed and low_read.risk_level == "low"

        # --- 17.8 P1: tool visibility + completion gate + deny_high ---
        from src.agent.completion_gate import (
            build_evidence_nudge_message,
            note_completion_nudge,
            should_block_completion,
        )
        from src.agent.task_state import TaskState, TestStatus
        from src.agent.tool_visibility import infer_phase, visible_tool_names

        assert infer_phase(todos_text="  [>] (1) 定位失败原因") == "explore"
        assert infer_phase(todos_text="  [>] (2) 修复 greeter.py") == "edit"
        assert infer_phase(todos_text="  [>] (3) 跑测试验证") == "verify"
        assert infer_phase(todos_text="", goal="hello") == "full"
        assert infer_phase(files_mutated=True, tests_passed=False) == "verify"

        explore_names = visible_tool_names(reg, "explore")
        assert "read_file" in explore_names and "write_file" not in explore_names
        assert "grep" in explore_names and "glob" in explore_names
        assert "git_status" in explore_names and "git_diff" in explore_names
        assert "run_shell" not in explore_names and "run_tests" not in explore_names
        verify_names = visible_tool_names(reg, "verify")
        assert "run_shell" in verify_names and "edit_file" in verify_names
        assert "run_tests" in verify_names
        assert "git_status" in verify_names and "git_diff" in verify_names
        assert "write_file" not in verify_names
        full_payload = reg.openai_tools()
        narrow = reg.openai_tools(names=explore_names)
        assert len(narrow) < len(full_payload)
        assert {t["function"]["name"] for t in narrow} == set(explore_names)

        ts = TaskState(goal="修复 greeter 测试", files_mutated=True, stop_condition="tests_all_pass")
        block, why = should_block_completion(ts, completion_mode="evidence", max_nudges=2)
        assert block and "evidence" in why
        note_completion_nudge(ts)
        note_completion_nudge(ts)
        block2, why2 = should_block_completion(ts, completion_mode="evidence", max_nudges=2)
        assert not block2 and "budget" in why2
        ts.test_status = TestStatus(passed=True, summary="1 passed")
        assert should_block_completion(ts, completion_mode="evidence")[0] is False
        assert "[completion_gate]" in build_evidence_nudge_message(TaskState(files_mutated=True))

        web_gate = PermissionGate(root, approval=ApprovalMode.AUTO, deny_high=True)
        web_gate.bind_registry(reg)
        high_denied = web_gate.authorize("run_shell", {"command": "git reset --hard"})
        assert not high_denied.allowed
        assert high_denied.risk_level == "high"
        med_ok = web_gate.authorize("run_shell", {"command": "pytest -q"})
        assert med_ok.allowed

        # Context trim keeps system + user and drops orphan tools
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "task"},
        ]
        for i in range(10):
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"c{i}",
                            "type": "function",
                            "function": {"name": "list_dir", "arguments": "{}"},
                        }
                    ],
                }
            )
            messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "ok"})
        trimmed = trim_messages(messages, max_messages=8)
        assert trimmed[0]["role"] == "system"
        assert trimmed[1]["role"] == "user"
        assert trimmed[2]["role"] != "tool"
        assert len(trimmed) <= 8
        # No orphan tool rows after trim
        for i, m in enumerate(trimmed):
            if m.get("role") != "tool":
                continue
            assert i > 0 and trimmed[i - 1].get("role") == "assistant" or any(
                prev.get("role") == "assistant" and prev.get("tool_calls")
                for prev in trimmed[:i]
            )

        from src.agent.context import sanitize_tool_pairing

        orphaned = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "x", "content": "orphan"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "list_dir", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c2",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
            # missing tool for c2 — should strip tool_calls
        ]
        fixed = sanitize_tool_pairing(orphaned)
        assert not any(m.get("role") == "tool" and m.get("tool_call_id") == "x" for m in fixed)
        assert any(m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in fixed)
        last_asst = [m for m in fixed if m.get("role") == "assistant"][-1]
        assert not last_asst.get("tool_calls")

        # Transcript
        tdir = root / "transcripts"
        result = AgentResult(
            final_text="done",
            steps=1,
            stopped_reason="completed",
            messages=[{"role": "user", "content": "hi"}],
            memory={
                "context_usage": {
                    "remaining_pct": 72,
                    "used_pct": 28,
                    "used_tokens": 2240,
                    "budget_tokens": 8000,
                    "level": "ok",
                    "scope": "turn",
                }
            },
        )
        path = save_transcript(tdir, task="hi", result=result, meta={"model": "test"})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["stopped_reason"] == "completed"
        assert data["task"] == "hi"
        assert data.get("context_usage", {}).get("remaining_pct") == 72

        sess_usage_result = AgentResult(
            final_text="turn done",
            steps=2,
            stopped_reason="completed",
            messages=[{"role": "user", "content": "t1"}],
            memory={
                "context_usage": {
                    "remaining_pct": 55,
                    "used_pct": 45,
                    "used_tokens": 3600,
                    "budget_tokens": 8000,
                    "level": "ok",
                    "scope": "turn",
                }
            },
        )
        save_transcript(
            tdir,
            task="t1",
            result=sess_usage_result,
            meta={"source": "test"},
            session_id="smokeusage0001",
        )
        sess_u = load_session(tdir, "smokeusage0001")
        assert sess_u is not None
        assert sess_u.get("context_usage", {}).get("remaining_pct") == 55
        assert sess_u["turns"][-1]["context_usage"]["remaining_pct"] == 55

        schemas = reg.openai_tools()
        assert len(schemas) == len(reg.names()) == 14
        assert any(s["function"]["name"] == "load_skill" for s in schemas)

        todo_out = reg.dispatch(
            "todo_write",
            {
                "todos": [
                    {"id": "1", "content": "read tests", "status": "completed"},
                    {"id": "2", "content": "fix code", "status": "in_progress"},
                ]
            },
        )
        assert "[x]" in todo_out and "[>]" in todo_out
        assert "1/2 completed" in todo_out

        bad_todo = reg.dispatch(
            "todo_write",
            {
                "todos": [
                    {"id": "1", "content": "a", "status": "in_progress"},
                    {"id": "2", "content": "b", "status": "in_progress"},
                ]
            },
        )
        assert bad_todo.startswith("Error")

        # Structured events from a tiny dry dispatch path via todo parse helper
        from src.agent.loop import _parse_todo_lines

        parsed = _parse_todo_lines(
            "Todo list:\n  [>] (1) a\n  [ ] (2) b\nProgress: 0/2 completed"
        )
        assert parsed and parsed[0]["status"] == "in_progress"

        # --- ACON-style Context Manager ---
        from src.agent.compress import compress_tool_result, related_test_paths
        from src.agent.context_manager import ContextManager

        pytest_log = (
            "============================= test session starts ==============================\n"
            + ("x" * 200 + "\n") * 40
            + "FAILED tests/test_divide.py::test_divide - ZeroDivisionError: division by zero\n"
            + "FAILED tests/test_add.py::test_add - AssertionError: assert 1 == 2\n"
            + "=========================== 2 failed, 1 passed in 0.12s ===========================\n"
            + "exit_code: 1\n"
        )
        compact = compress_tool_result("run_shell", pytest_log)
        assert "2 failed" in compact
        assert "test_divide" in compact
        assert "ZeroDivisionError" in compact or "FAILED" in compact
        assert len(compact) < len(pytest_log) // 2

        assert "test_calc.py" in related_test_paths("calc.py") or "test_calc.py" in related_test_paths(
            "src/calc.py"
        )

        ctx = ContextManager(
            workdir=root,
            tool_names=reg.names(),
            token_budget=2500,
            recent_keep_messages=8,
        )
        stored = ctx.observe_tool(
            step=1,
            tool_name="run_shell",
            raw_args={"command": "pytest"},
            result=pytest_log,
        )
        assert len(stored) < len(pytest_log)
        assert ctx.state.last_errors

        stored2 = ctx.observe_tool(
            step=2,
            tool_name="edit_file",
            raw_args={"path": "calc.py", "old_string": "a", "new_string": "b"},
            result="Edited calc.py: replaced 1 occurrence(s).",
        )
        assert "calc.py" in ctx.state.focus_files
        assert "Edited" in stored2

        long_msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "fix calculator"},
        ]
        for i in range(12):
            long_msgs.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"t{i}",
                            "type": "function",
                            "function": {
                                "name": "run_shell",
                                "arguments": '{"command":"pytest"}',
                            },
                        }
                    ],
                }
            )
            long_msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": f"t{i}",
                    "content": pytest_log,
                }
            )
        prepared = ctx.prepare_messages(long_msgs, user_task="fix calculator")
        blob = "\n".join(str(m.get("content") or "") for m in prepared)
        assert "Current State" in blob or "Context Manager" in blob
        assert "Progressive Disclosure" in prepared[0]["content"]
        # P1: working-memory note is a variable SUFFIX (last message)
        assert prepared[-1]["role"] == "user"
        assert "Context Manager" in str(prepared[-1].get("content") or "")
        assert "prefix_stable" in str(prepared[-1].get("content") or "")
        # System prompt cached / stable across prepares
        p2 = ctx.prepare_messages(long_msgs, user_task="fix calculator")
        assert p2[0]["content"] == prepared[0]["content"]

        # --- P0/P1 long/short memory ---
        from src.agent.memory import (
            append_run_to_memory,
            load_memory_excerpt,
            load_working_memory,
            resolve_memory_path,
            save_working_memory,
            search_memory_sources,
        )

        mem_path = append_run_to_memory(
            root,
            task="fix greeter tests",
            final_text="Fixed greet() to return Hello, name!",
            stopped_reason="completed",
            memory={
                "focus_files": ["greeter.py", "greeter_test.py"],
                "last_errors": ["AssertionError: Hi vs Hello"],
                "history_summary": "Phase done (todo 1):\n- edit_file greeter.py (ok)",
                "todos_text": "Todo list:\n  [x] (1) fix\nProgress: 1/1 completed",
            },
        )
        assert mem_path is not None and mem_path.is_file()
        assert mem_path == resolve_memory_path(root)
        excerpt = load_memory_excerpt(root)
        assert "greeter.py" in excerpt
        assert "Hello" in excerpt or "Conclusion" in excerpt

        wm = save_working_memory(
            root,
            {
                "task": "fix greeter tests",
                "focus_files": ["greeter.py"],
                "history_summary": "edited greeter",
                "todos_text": "",
                "last_errors": [],
                "actions": [],
            },
            transcript_dir=root / "transcripts",
        )
        assert wm is not None and wm.is_file()
        loaded_wm = load_working_memory(root)
        assert loaded_wm and loaded_wm.get("focus_files") == ["greeter.py"]
        assert (root / "transcripts" / "working_memory.json").is_file()

        ctx2 = ContextManager(workdir=root, tool_names=reg.names(), token_budget=4000)
        assert ctx2.state.project_memory
        assert "Project Memory" in ctx2.state.render_current_state()

        # Phase compression when a todo becomes completed
        ctx3 = ContextManager(workdir=root, tool_names=reg.names(), token_budget=4000)
        ctx3.observe_tool(
            step=1,
            tool_name="todo_write",
            raw_args={},
            result=(
                "Todo list:\n  [>] (1) read tests\n  [ ] (2) fix\n"
                "Progress: 0/2 completed"
            ),
        )
        ctx3.observe_tool(
            step=2,
            tool_name="read_file",
            raw_args={"path": "greeter_test.py"},
            result="def test_greet():\n    assert True\n",
        )
        ctx3.observe_tool(
            step=3,
            tool_name="todo_write",
            raw_args={},
            result=(
                "Todo list:\n  [x] (1) read tests\n  [>] (2) fix\n"
                "Progress: 1/2 completed"
            ),
        )
        assert ctx3.state.phase_compress_events >= 1
        assert "Phase done" in ctx3.state.history_summary
        assert "todo 1" in ctx3.state.history_summary

        # Fold → re-inject root state (MEMORY + focus + open todos)
        ctx3.state.focus_files = ["greeter.py"]
        folded = ctx3.prepare_messages(long_msgs, user_task="fix calculator")
        folded_blob = "\n".join(str(m.get("content") or "") for m in folded)
        assert ctx3.state.fold_events >= 1 or "Root State" in folded_blob or "Historical" in folded_blob
        # Force fold path with tiny budget
        ctx4 = ContextManager(
            workdir=root,
            tool_names=reg.names(),
            token_budget=2000,
            recent_keep_messages=6,
        )
        ctx4.state.focus_files = ["greeter.py"]
        ctx4.state.todos_text = (
            "Todo list:\n  [x] (1) read\n  [>] (2) fix\nProgress: 1/2 completed"
        )
        ctx4.reload_project_memory()
        folded2 = ctx4.prepare_messages(long_msgs, user_task="fix calculator")
        assert folded2[-1]["role"] == "user"
        assert "Root State" in str(folded2[-1].get("content") or "")
        assert "greeter.py" in str(folded2[-1].get("content") or "")
        assert "(2)" in str(folded2[-1].get("content") or "")  # incomplete todo

        # memory_search over MEMORY.md
        found = search_memory_sources(workdir=root, query="greeter Hello", transcript_dir=None)
        assert "greeter" in found.lower()
        tool_found = reg.dispatch("memory_search", {"query": "greeter"})
        assert "memory_search results" in tool_found or "greeter" in tool_found.lower()

        # --- P2: RAG / MicroCompact / ACON guideline / cache policy ---
        from src.agent.acon_guideline import load_guideline, record_failure_pair
        from src.agent.cache_policy import build_cache_policy
        from src.agent.compress import compress_tool_result, microcompact_messages, stub_tool_result
        from src.agent.rag import build_rag_index, rag_search

        stub = stub_tool_result("run_shell", "x" * 2000)
        assert stub.startswith("[stub:run_shell")
        summarized = compress_tool_result(
            "run_shell",
            pytest_log,
            tier="summary",
            soft_limit=800,
        )
        assert len(summarized) < len(pytest_log)
        assert "failed" in summarized.lower() or "FAILED" in summarized

        # MicroCompact stubs older tool messages
        mc_msgs = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "t"},
        ]
        for i in range(8):
            mc_msgs.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"m{i}",
                            "type": "function",
                            "function": {"name": "run_shell", "arguments": "{}"},
                        }
                    ],
                }
            )
            mc_msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": f"m{i}",
                    "content": pytest_log,
                }
            )
        compacted, n_stubs = microcompact_messages(mc_msgs, keep_recent_tools=2)
        assert n_stubs >= 1
        assert any(
            isinstance(m.get("content"), str) and m["content"].startswith("[stub:")
            for m in compacted
            if m.get("role") == "tool"
        )

        # Context manager applies microcompact under budget pressure
        ctx_mc = ContextManager(
            workdir=root,
            tool_names=reg.names(),
            token_budget=3500,
            recent_keep_messages=8,
        )
        prepared_mc = ctx_mc.prepare_messages(mc_msgs, user_task="t")
        assert ctx_mc.state.microcompact_events >= 1 or any(
            isinstance(m.get("content"), str) and "[stub:" in str(m.get("content"))
            for m in prepared_mc
        )

        g0 = record_failure_pair(
            root,
            tool_name="run_shell",
            observation_preview=("y" * 500) + "\nexit_code: 1\nFAILED tests/x.py",
            recovered=False,
        )
        assert g0.get("updates", 0) >= 1
        g1 = load_guideline(root)
        assert "run_shell" in (g1.get("tool_limits") or {})

        idx = build_rag_index(root, transcript_dir=root / "transcripts")
        assert idx.get("n_docs", 0) >= 1
        rag_hit = rag_search(root, "greeter hello", rebuild=False)
        assert "rag_search" in rag_hit
        rag_tool = reg.dispatch("rag_search", {"query": "greeter", "rebuild": True})
        assert "rag_search" in rag_tool or "greeter" in rag_tool.lower()

        pol = build_cache_policy(workdir=str(root), model="test-model")
        assert pol.layout == "prefix_stable_suffix_variable"
        assert pol.openai_extra_body() is not None

        # Continue slim: memory + recent K + original task (not full dump)
        tdir = root / "sessions"
        big_messages: list[dict] = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "original task"},
        ]
        for i in range(20):
            big_messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"c{i}",
                            "type": "function",
                            "function": {"name": "list_dir", "arguments": "{}"},
                        }
                    ],
                }
            )
            big_messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "ok"})
        sess_result = AgentResult(
            final_text="turn1 done",
            steps=3,
            stopped_reason="completed",
            messages=big_messages,
            memory={
                "task": "original task",
                "focus_files": ["a.py"],
                "history_summary": "did stuff",
                "last_errors": [],
                "todos_text": "",
                "actions": [],
            },
        )
        save_transcript(
            tdir,
            task="original task",
            result=sess_result,
            meta={"source": "test"},
            session_id="smokecontinue01",
        )
        sess = load_session(tdir, "smokecontinue01")
        assert sess is not None
        slim, prior_mem = build_continue_context(sess, recent_k=6)
        assert prior_mem and prior_mem.get("focus_files") == ["a.py"]
        assert slim[0]["role"] == "user" and (
            "original task" in slim[0]["content"] or "ACTIVE GOAL" in slim[0]["content"]
        )
        assert any(
            m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and m["content"].startswith("[Session memory")
            for m in slim
        )

        # 「继续做」must resolve to latest unfinished goal, not turn-0
        from src.agent.transcript import last_active_task, resolve_continue_task

        multi = {
            "task": "first hello task",
            "turns": [
                {"task": "first hello task", "stopped_reason": "completed", "steps": 2},
                {
                    "task": "beautify timer UI",
                    "stopped_reason": "max_steps",
                    "steps": 20,
                },
            ],
            "messages": [
                {"role": "user", "content": "first hello task"},
                {"role": "assistant", "content": "done hello"},
                {"role": "user", "content": "beautify timer UI"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "1", "content": "ui code"},
            ],
            "memory": {"task": "继续做", "focus_files": ["timer_app.py"]},
        }
        assert last_active_task(multi) == "beautify timer UI"
        resolved = resolve_continue_task("继续做", multi)
        assert "beautify timer UI" in resolved
        assert "hello" not in resolved.lower() or "first hello" not in resolved
        slim2, mem2 = build_continue_context(multi, recent_k=4)
        assert "ACTIVE GOAL" in slim2[0]["content"]
        assert "beautify timer UI" in slim2[0]["content"]
        assert mem2 and mem2.get("task") == "beautify timer UI"
        assert any(
            m.get("role") == "user" and m.get("content") == "beautify timer UI" for m in slim2
        )
        # Slim continue must keep an ACTIVE GOAL briefing (not revive only turn-0)
        assert any(
            isinstance(m.get("content"), str) and "ACTIVE GOAL" in m["content"] for m in slim
        )

    print("smoke_v1: OK (ui-events+context+memory-p0+p1+p2)")


if __name__ == "__main__":
    main()
