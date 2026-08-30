"""Quick check: run_shell survives UTF-8 bytes that break GBK text=True."""

from __future__ import annotations

from pathlib import Path
import tempfile

from src.agent.permissions import ApprovalMode, PermissionGate
from src.tools import build_default_registry
from src.tools.shell import _decode_output


def main() -> None:
    assert _decode_output(b"\xe2\x80\x94")  # em-dash UTF-8
    with tempfile.TemporaryDirectory() as tmp:
        gate = PermissionGate(Path(tmp), approval=ApprovalMode.AUTO)
        reg = build_default_registry(gate)
        out = reg.dispatch(
            "run_shell",
            {
                "command": (
                    "python -c "
                    "\"import sys; sys.stdout.buffer.write(b'hi-\\xe2\\x80\\x94\\n')\""
                )
            },
        )
        assert "exit_code: 0" in out, out
        assert "hi-" in out, out
        assert "UnicodeDecodeError" not in out, out
        print("shell_encoding_ok")


if __name__ == "__main__":
    main()
