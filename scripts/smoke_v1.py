"""Offline smoke tests for Day2 harness pieces (no API key required).

Run: python -m scripts.smoke_v1
"""

from __future__ import annotations

import json
import tempfile
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
            "glob",
            "list_dir",
            "memory_search",
            "rag_search",
            "read_file",
            "run_shell",
            "todo_write",
            "write_file",
        }
        assert set(reg.names()) == expected, reg.names()

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

        written = reg.dispatch(
            "write_file",
            {"path": "sub/out.py", "content": "print(42)\n"},
        )
        assert "out.py" in written

        shell = reg.dispatch("run_shell", {"command": "python -c \"print('ok')\""})
        assert "exit_code: 0" in shell
        assert "ok" in shell

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
        risky = never_gate.authorize("run_shell", {"command": "rm -rf build"})
        assert not risky.allowed

        # ask mode with auto-deny callback
        ask_gate = PermissionGate(
            root,
            approval=ApprovalMode.ASK,
            ask_fn=lambda _p: False,
        )
        blocked = ask_gate.authorize("write_file", {"path": "x", "content": "y"})
        assert not blocked.allowed

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

        # Transcript
        tdir = root / "transcripts"
        result = AgentResult(
            final_text="done",
            steps=1,
            stopped_reason="completed",
            messages=[{"role": "user", "content": "hi"}],
        )
        path = save_transcript(tdir, task="hi", result=result, meta={"model": "test"})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["stopped_reason"] == "completed"
        assert data["task"] == "hi"

        schemas = reg.openai_tools()
        assert len(schemas) == 9

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
        assert slim[0]["role"] == "user" and "original task" in slim[0]["content"]
        assert any(
            m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and m["content"].startswith("[Session memory")
            for m in slim
        )
        assert len(slim) < len(big_messages)

    print("smoke_v1: OK (ui-events+context+memory-p0+p1+p2)")


if __name__ == "__main__":
    main()
