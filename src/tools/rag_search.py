"""rag_search — local TF–IDF semantic recall over code / MEMORY / transcripts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agent.rag import rag_search
from src.tools.base import FunctionTool, ToolRegistry


def register_rag_search_tools(
    registry: ToolRegistry,
    *,
    workdir: Path,
    transcript_dir: Path | None = None,
) -> None:
    def _rag_search(args: dict[str, Any]) -> str:
        query = args.get("query")
        if query is None:
            return "Error: missing required argument 'query'"
        top_k = args.get("top_k", 5)
        rebuild = bool(args.get("rebuild", False))
        try:
            top_k_i = int(top_k)
        except (TypeError, ValueError):
            top_k_i = 5
        return rag_search(
            workdir,
            str(query),
            transcript_dir=transcript_dir,
            top_k=top_k_i,
            rebuild=rebuild,
        )

    registry.register(
        FunctionTool(
            name="rag_search",
            description=(
                "Local semantic search (TF–IDF, no cloud embeddings) over the workdir "
                "source files, MEMORY.md, and recent transcripts. Use when keyword "
                "memory_search is not enough and you need related code/docs by meaning. "
                "Set rebuild=true to refresh the index after big file changes."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language or keyword query.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "How many hits to return (default 5).",
                    },
                    "rebuild": {
                        "type": "boolean",
                        "description": "Rebuild the local index before searching.",
                    },
                },
                "required": ["query"],
            },
            handler=_rag_search,
        )
    )
