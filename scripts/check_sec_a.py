"""Offline Security Sec-A/B sanity check (no API).

Run:
  python -m scripts.check_sec_a
  python -m scripts.smoke_v1
"""

from __future__ import annotations

from pathlib import Path

from src.agent.permissions import (
    ApprovalMode,
    PermissionGate,
    assess_shell_risk,
    looks_like_network_or_install,
)


def main() -> None:
    root = Path(".").resolve()

    print("=== Sec-A: network/install detection ===")
    assert looks_like_network_or_install("pip install requests")
    assert looks_like_network_or_install("python -m pip install -e .")
    assert looks_like_network_or_install("npm install lodash")
    assert looks_like_network_or_install("curl https://example.com")
    assert looks_like_network_or_install("Invoke-WebRequest https://x")
    assert not looks_like_network_or_install("pytest -q greeter_test.py")
    assert not looks_like_network_or_install("python -m unittest")

    risk_h, deny_h = assess_shell_risk("pip install requests", network_policy="high")
    assert risk_h == "high" and deny_h is None, (risk_h, deny_h)

    risk_d, deny_d = assess_shell_risk("pip install requests", network_policy="deny")
    assert risk_d == "high" and deny_d and "NETWORK_POLICY=deny" in deny_d, (risk_d, deny_d)

    risk_a, deny_a = assess_shell_risk("pip install requests", network_policy="allow")
    assert risk_a == "medium" and deny_a is None, (risk_a, deny_a)

    risk_c, _ = assess_shell_risk("curl https://example.com/x", network_policy="high")
    assert risk_c == "high"
    print("ok network policy")

    print("=== Sec-A: gate authorize ===")
    gate_high = PermissionGate(root, approval=ApprovalMode.AUTO, network_policy="high")
    pip_auto = gate_high.authorize("run_shell", {"command": "pip install requests"})
    assert pip_auto.allowed and pip_auto.risk_level == "high"

    gate_deny = PermissionGate(root, approval=ApprovalMode.AUTO, network_policy="deny")
    pip_deny = gate_deny.authorize("run_shell", {"command": "pip install requests"})
    assert not pip_deny.allowed and "NETWORK_POLICY" in pip_deny.reason

    web = PermissionGate(
        root, approval=ApprovalMode.AUTO, deny_high=True, network_policy="high"
    )
    pip_web = web.authorize("run_shell", {"command": "npm install left-pad"})
    assert not pip_web.allowed and pip_web.risk_level == "high"

    pytest_ok = gate_high.authorize("run_shell", {"command": "pytest -q greeter_test.py"})
    assert pytest_ok.allowed and pytest_ok.risk_level == "medium"
    print("ok gate network")

    print("=== Sec-B: subprocess sensitive read ===")
    py_env = gate_high.authorize(
        "run_shell",
        {"command": "python -c \"print(open('.env').read())\""},
    )
    assert not py_env.allowed, py_env.reason
    assert "sensitive" in py_env.reason.lower() or "读密" in py_env.reason

    node_env = gate_high.authorize(
        "run_shell",
        {"command": "node -e \"require('fs').readFileSync('.env')\""},
    )
    assert not node_env.allowed, node_env.reason

    head_env = gate_high.authorize("run_shell", {"command": "head .env"})
    assert not head_env.allowed, head_env.reason

    # Non-sensitive interpreter one-liner still medium (allowed under auto)
    py_ok = gate_high.authorize(
        "run_shell",
        {"command": "python -c \"print(1+1)\""},
    )
    assert py_ok.allowed and py_ok.risk_level == "medium", (py_ok.allowed, py_ok.risk_level)

    # Regression: cat .env / read_file .env
    cat = gate_high.authorize("run_shell", {"command": "cat .env"})
    assert not cat.allowed
    rf = gate_high.authorize("read_file", {"path": ".env"})
    assert not rf.allowed
    print("ok subprocess sensitive")

    print("=== regression: reset / pytest ===")
    reset = gate_high.authorize("run_shell", {"command": "git reset --hard"})
    assert reset.risk_level == "high"
    print("ok regression")

    print("OK")


if __name__ == "__main__":
    main()
