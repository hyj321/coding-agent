"""ask_user tool (C9): block until the user answers a clarifying question."""

from __future__ import annotations

from typing import Any, Callable

from src.tools.base import FunctionTool, ToolRegistry

UserAskFn = Callable[[str], str]


def register_user_ask_tools(
    registry: ToolRegistry,
    ask_fn: UserAskFn | None = None,
) -> None:
    def ask_user(args: dict[str, Any]) -> str:
        question = args.get("question")
        if not question or not isinstance(question, str):
            return "Error: missing required string argument 'question'"
        if ask_fn is None:
            return (
                "Error: ask_user is not available in this runtime "
                "(no interactive handler configured)"
            )
        return ask_fn(question.strip())

    registry.register(
        FunctionTool(
            name="ask_user",
            description=(
                "Ask the human user a clarifying question when requirements, "
                "paths, or preferences are missing. Blocks until they reply. "
                "Use sparingly — prefer grep/read_file when the repo can answer."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Clear, specific question for the user.",
                    }
                },
                "required": ["question"],
            },
            handler=ask_user,
            risk_level="low",
            is_readonly=True,
            destructive=False,
            network=False,
            open_world=False,
        )
    )
