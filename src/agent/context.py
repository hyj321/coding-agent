"""Conversation / system-prompt helpers + legacy history trimming.

Prefer ContextManager (context_manager.py) for ACON-style layered memory.
trim_messages remains as a simple fallback / unit-test helper.
"""

from __future__ import annotations

from typing import Any

from src.agent.context_manager import (
    ContextManager,
    build_system_prompt,
    expand_tail_for_tool_pairing,
    sanitize_tool_pairing,
)

__all__ = [
    "ContextManager",
    "build_system_prompt",
    "expand_tail_for_tool_pairing",
    "sanitize_tool_pairing",
    "trim_messages",
    "truncate_text",
]


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, total {len(text)} chars]"


def trim_messages(
    messages: list[dict[str, Any]],
    *,
    max_messages: int,
) -> list[dict[str, Any]]:
    """Keep system + original user task, plus a recent tail that respects tool pairing.

    Does not mutate the caller's list. If already within budget, returns as-is.
    """
    if max_messages < 4 or len(messages) <= max_messages:
        return sanitize_tool_pairing(messages)

    head = messages[:2]  # system + first user
    budget = max_messages - len(head)
    if budget < 2:
        budget = 2

    tail = messages[-budget:]
    tail = expand_tail_for_tool_pairing(messages, tail, min_index=len(head))

    if not tail:
        return sanitize_tool_pairing(list(messages[:max_messages]))

    trimmed = head + list(tail)
    if len(trimmed) == len(messages):
        return sanitize_tool_pairing(messages)

    notice = {
        "role": "user",
        "content": (
            "[system note] Earlier conversation turns were trimmed to fit the context "
            "budget. Continue from the recent tool results and the original task."
        ),
    }
    if len(trimmed) + 1 <= max_messages:
        return sanitize_tool_pairing(head + [notice] + list(tail))
    return sanitize_tool_pairing(trimmed)
