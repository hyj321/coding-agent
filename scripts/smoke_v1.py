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
from src.agent.transcript import save_transcript
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
        assert len(schemas) == 7

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

    print("smoke_v1: OK (ui-events)")


if __name__ == "__main__":
    main()
