"""Capability / permission snapshot for T1 (M-T1)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.agent.permissions import ApprovalMode, PermissionGate, parse_approval_mode, parse_network_policy
from src.agent.shell_allowlist import (
    DEFAULT_SHELL_ALLOWLIST_PREFIXES,
    load_shell_allowlist_from_env,
    parse_shell_mode,
)
from src.tools import build_default_registry
from src.tools.base import ToolRegistry

_TOOL_CATEGORIES: dict[str, str] = {
    "read_file": "File",
    "write_file": "File",
    "edit_file": "File",
    "list_dir": "File",
    "glob": "Search",
    "grep": "Search",
    "run_shell": "Execution",
    "run_tests": "Execution",
    "git_status": "Git",
    "git_diff": "Git",
    "todo_write": "Orchestration",
    "load_skill": "Orchestration",
    "ask_user": "Orchestration",
    "list_styles": "Style",
    "load_style": "Style",
    "save_style": "Style",
    "refine_style": "Style",
    "delete_style": "Style",
    "memory_search": "Memory",
    "rag_search": "Memory",
}

_CAPABILITY_BOUNDARIES: tuple[str, ...] = (
    "No browser / no external web search",
    "No LSP / symbol jump",
    "No git commit or push (read-only git tools only)",
    "No OS-level sandbox — workdir path sandbox only",
    "run_shell remains the widest execution surface when SHELL_MODE=open",
)


def load_runtime_policies() -> dict[str, Any]:
    """Read harness policy knobs from env (no API key required)."""
    load_dotenv()
    visibility = (os.getenv("TOOL_VISIBILITY") or "auto").strip().lower()
    if visibility in {"full", "none"}:
        visibility = "off"
    completion = (os.getenv("COMPLETION_MODE") or "evidence").strip().lower()
    fake_green = (os.getenv("FAKE_GREEN_MODE") or "block").strip().lower()
    shell_mode = parse_shell_mode(os.getenv("SHELL_MODE"))
    allowlist = load_shell_allowlist_from_env()
    deny_high = (os.getenv("DENY_HIGH") or "").strip().lower() in {"1", "true", "yes", "on"}
    return {
        "approval": parse_approval_mode(os.getenv("APPROVAL")).value,
        "network_policy": parse_network_policy(os.getenv("NETWORK_POLICY")),
        "shell_mode": shell_mode,
        "shell_allowlist_prefixes": list(allowlist),
        "tool_visibility": visibility,
        "completion_mode": completion,
        "fake_green_mode": fake_green,
        "deny_high": deny_high,
        "max_task_tokens": int(os.getenv("MAX_TASK_TOKENS", "0") or "0"),
    }


def _tool_rows(registry: ToolRegistry) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in registry.names():
        tool = registry.get(name)
        if tool is None:
            continue
        rows.append(
            {
                "name": name,
                "category": _TOOL_CATEGORIES.get(name, "Other"),
                "risk_level": getattr(tool, "risk_level", "medium"),
                "is_readonly": bool(getattr(tool, "is_readonly", False)),
                "destructive": bool(getattr(tool, "destructive", False)),
                "network": bool(getattr(tool, "network", False)),
                "open_world": bool(getattr(tool, "open_world", False)),
            }
        )
    return rows


def build_capability_snapshot(
    workdir: Path,
    *,
    approval: ApprovalMode | str | None = None,
    network_policy: str | None = None,
    shell_mode: str | None = None,
    deny_high: bool | None = None,
    tool_visibility: str | None = None,
    completion_mode: str | None = None,
    fake_green_mode: str | None = None,
    shell_allowlist_prefixes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable capability + policy snapshot."""
    policies = load_runtime_policies()
    wd = workdir.resolve()

    mode = approval if approval is not None else policies["approval"]
    if isinstance(mode, str):
        mode = parse_approval_mode(mode)

    net = network_policy if network_policy is not None else policies["network_policy"]
    shell = shell_mode if shell_mode is not None else policies["shell_mode"]
    prefixes = (
        shell_allowlist_prefixes
        if shell_allowlist_prefixes is not None
        else tuple(policies["shell_allowlist_prefixes"])
    )
    high_deny = deny_high if deny_high is not None else policies["deny_high"]

    gate = PermissionGate(
        wd,
        approval=mode,
        deny_high=high_deny,
        network_policy=net,
        shell_mode=shell,  # type: ignore[arg-type]
        shell_allowlist_prefixes=prefixes,
    )
    registry = build_default_registry(gate)

    return {
        "workdir": str(wd),
        "tool_count": len(registry.names()),
        "policies": {
            "approval": gate.approval.value,
            "network_policy": gate.network_policy,
            "shell_mode": gate.shell_mode,
            "shell_allowlist_prefixes": list(gate.shell_allowlist_prefixes),
            "tool_visibility": tool_visibility or policies["tool_visibility"],
            "completion_mode": completion_mode or policies["completion_mode"],
            "fake_green_mode": fake_green_mode or policies["fake_green_mode"],
            "deny_high": gate.deny_high,
            "max_task_tokens": policies["max_task_tokens"],
        },
        "tools": _tool_rows(registry),
        "boundaries": list(_CAPABILITY_BOUNDARIES),
        "web_defaults": {
            "approval": "ask",
            "ask_min_risk": "medium",
            "deny_high": False,
            "note": "Web runs override CLI approval to ask; user can Allow High risk.",
        },
    }


def format_capability_report(snapshot: dict[str, Any]) -> str:
    """Plain-text report for CLI."""
    lines = [
        f"workdir: {snapshot['workdir']}",
        f"tools: {snapshot['tool_count']}",
        "",
        "Policies:",
    ]
    pol = snapshot["policies"]
    for key in (
        "approval",
        "network_policy",
        "shell_mode",
        "tool_visibility",
        "completion_mode",
        "fake_green_mode",
        "deny_high",
        "max_task_tokens",
    ):
        lines.append(f"  {key}: {pol[key]}")
    if pol.get("shell_mode") == "allowlist":
        lines.append("  shell_allowlist_prefixes:")
        for p in pol.get("shell_allowlist_prefixes") or DEFAULT_SHELL_ALLOWLIST_PREFIXES:
            lines.append(f"    - {p}")
    lines.append("")
    lines.append("Tools:")
    for t in snapshot.get("tools") or []:
        ro = "readonly" if t.get("is_readonly") else "mutating"
        hints = []
        if t.get("destructive"):
            hints.append("destructive")
        if t.get("network"):
            hints.append("network")
        if t.get("open_world"):
            hints.append("open_world")
        hint_s = f" [{', '.join(hints)}]" if hints else ""
        lines.append(
            f"  [{t.get('category', '?')}] {t['name']} "
            f"({t.get('risk_level', '?')}, {ro}){hint_s}"
        )
    lines.append("")
    lines.append("Boundaries:")
    for b in snapshot.get("boundaries") or []:
        lines.append(f"  - {b}")
    return "\n".join(lines)
