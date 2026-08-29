"""Offline smoke tests for V1 tools / sandbox (no API key required).

Run: python -m scripts.smoke_v1
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.agent.permissions import PermissionGate, SandboxError
from src.tools import build_default_registry


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "notes.txt").write_text("hello", encoding="utf-8")
        (root / "sub").mkdir()

        gate = PermissionGate(root)
        reg = build_default_registry(gate, max_output_chars=2000)

        assert set(reg.names()) == {"list_dir", "read_file", "run_shell", "write_file"}

        listed = reg.dispatch("list_dir", {"path": "."})
        assert "notes.txt" in listed and "sub" in listed

        content = reg.dispatch("read_file", {"path": "notes.txt"})
        assert content == "hello"

        written = reg.dispatch(
            "write_file",
            {"path": "sub/out.py", "content": "print(42)\n"},
        )
        assert "out.py" in written
        assert (root / "sub" / "out.py").read_text(encoding="utf-8") == "print(42)\n"

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

        schemas = reg.openai_tools()
        assert len(schemas) == 4
        assert all(s["type"] == "function" for s in schemas)

    print("smoke_v1: OK")


if __name__ == "__main__":
    main()
