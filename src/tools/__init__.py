"""Assemble default tools for the agent.

Call `build_default_registry` from the CLI / loop setup so new tools
can be added in one place later.
"""

from __future__ import annotations

from pathlib import Path

from src.agent.permissions import PermissionGate
from src.tools.base import ToolRegistry
from src.tools.filesystem import register_filesystem_tools
from src.tools.git_tools import register_git_tools
from src.tools.memory_search import register_memory_search_tools
from src.tools.rag_search import register_rag_search_tools
from src.tools.shell import register_shell_tools
from src.tools.user_ask import UserAskFn, register_user_ask_tools
from src.tools.skills import register_skill_tools
from src.tools.testing import register_testing_tools
from src.tools.todo import TodoStore, register_todo_tools


def build_default_registry(
    gate: PermissionGate,
    *,
    max_output_chars: int = 8000,
    transcript_dir: Path | None = None,
    ask_user_fn: UserAskFn | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    register_filesystem_tools(registry, gate, max_output_chars=max_output_chars)
    register_shell_tools(registry, gate, max_output_chars=max_output_chars)
    register_testing_tools(registry, gate, max_output_chars=max_output_chars)
    register_git_tools(registry, gate, max_output_chars=max_output_chars)
    register_todo_tools(registry, TodoStore())
    register_skill_tools(registry)
    register_user_ask_tools(registry, ask_fn=ask_user_fn)
    register_memory_search_tools(
        registry,
        workdir=gate.workdir,
        transcript_dir=transcript_dir,
    )
    register_rag_search_tools(
        registry,
        workdir=gate.workdir,
        transcript_dir=transcript_dir,
    )
    gate.bind_registry(registry)
    return registry
