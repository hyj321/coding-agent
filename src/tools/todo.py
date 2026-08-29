"""Todo / plan tool — explicit checklist so the agent does not drift.

Session-scoped in-memory store. Each agent run gets a fresh store via registry build.
"""

from __future__ import annotations

from typing import Any

from src.tools.base import FunctionTool, ToolRegistry

_ALLOWED_STATUS = frozenset({"pending", "in_progress", "completed", "cancelled"})


class TodoStore:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def replace(self, todos: list[dict[str, Any]]) -> str:
        if not isinstance(todos, list) or not todos:
            return "Error: 'todos' must be a non-empty list of objects"

        cleaned: list[dict[str, str]] = []
        for i, item in enumerate(todos):
            if not isinstance(item, dict):
                return f"Error: todos[{i}] must be an object"
            content = str(item.get("content") or "").strip()
            status = str(item.get("status") or "pending").strip().lower()
            item_id = str(item.get("id") or f"{i + 1}").strip()
            if not content:
                return f"Error: todos[{i}].content is required"
            if status not in _ALLOWED_STATUS:
                return (
                    f"Error: todos[{i}].status must be one of "
                    f"{', '.join(sorted(_ALLOWED_STATUS))}"
                )
            cleaned.append({"id": item_id, "content": content, "status": status})

        in_progress = [t for t in cleaned if t["status"] == "in_progress"]
        if len(in_progress) > 1:
            return "Error: at most one todo may be in_progress at a time"

        self.items = cleaned
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "(todo list empty)"
        lines = ["Todo list:"]
        for t in self.items:
            mark = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
                "cancelled": "[-]",
            }.get(t["status"], "[?]")
            lines.append(f"  {mark} ({t['id']}) {t['content']}")
        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"Progress: {done}/{len(self.items)} completed")
        return "\n".join(lines)


def register_todo_tools(registry: ToolRegistry, store: TodoStore | None = None) -> TodoStore:
    store = store or TodoStore()

    def todo_write(args: dict[str, Any]) -> str:
        todos = args.get("todos")
        if todos is None:
            return "Error: missing required argument 'todos'"
        return store.replace(todos)

    registry.register(
        FunctionTool(
            name="todo_write",
            description=(
                "Create or replace the agent's task checklist for the current run. "
                "Use this to plan before acting on non-trivial tasks, then update statuses "
                "as you progress. Prefer exactly one item with status in_progress. "
                "Statuses: pending | in_progress | completed | cancelled."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "Full todo list (replaces the previous list).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Short stable id, e.g. '1'.",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "What to do.",
                                },
                                "status": {
                                    "type": "string",
                                    "description": "pending | in_progress | completed | cancelled",
                                },
                            },
                            "required": ["content", "status"],
                        },
                    }
                },
                "required": ["todos"],
            },
            handler=todo_write,
        )
    )
    return store
