"""Assemble default tools for the agent.

Call `build_default_registry` from the CLI / loop setup so new tools
can be added in one place later.
"""

from __future__ import annotations

from src.agent.permissions import PermissionGate
from src.tools.base import ToolRegistry
from src.tools.filesystem import register_filesystem_tools
from src.tools.shell import register_shell_tools
from src.tools.todo import TodoStore, register_todo_tools


def build_default_registry(
    gate: PermissionGate,
    *,
    max_output_chars: int = 8000,
) -> ToolRegistry:
    registry = ToolRegistry()
    register_filesystem_tools(registry, gate, max_output_chars=max_output_chars)
    register_shell_tools(registry, gate, max_output_chars=max_output_chars)
    register_todo_tools(registry, TodoStore())
    return registry
