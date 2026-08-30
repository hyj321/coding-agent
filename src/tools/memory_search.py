"""memory_search — keyword recall over MEMORY.md + transcripts (no vectors)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agent.memory import search_memory_sources
from src.tools.base import FunctionTool, ToolRegistry


def register_memory_search_tools(
    registry: ToolRegistry,
    *,
    workdir: Path,
    transcript_dir: Path | None = None,
) -> None:
    def memory_search(args: dict[str, Any]) -> str:
        query = args.get("query")
        if query is None:
            return "Error: missing required argument 'query'"
        max_hits = args.get("max_hits", 12)
        try:
            max_hits_i = int(max_hits)
        except (TypeError, ValueError):
            max_hits_i = 12
        max_hits_i = max(1, min(max_hits_i, 30))
        return search_memory_sources(
            workdir=workdir,
            query=str(query),
            transcript_dir=transcript_dir,
            max_hits=max_hits_i,
        )

    registry.register(
        FunctionTool(
            name="memory_search",
            description=(
                "Search durable project memory (MEMORY.md) and past run/session "
                "transcripts by keyword. Use when you need prior conclusions, "
                "pitfalls, or what was changed in earlier tasks. Not for current "
                "open files — use read_file / glob for those."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords to search (space-separated).",
                    },
                    "max_hits": {
                        "type": "integer",
                        "description": "Max results to return (default 12, max 30).",
                    },
                },
                "required": ["query"],
            },
            handler=memory_search,
            risk_level="low",
            is_readonly=True,
        )
    )
