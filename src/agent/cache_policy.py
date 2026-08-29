"""Prompt-cache / server-compaction policy hooks (vendor-dependent).

DeepSeek and other OpenAI-compatible gateways may offer prompt caching when
the *prefix* is stable. We already keep system+task stable and put working
memory at the suffix. This module:

1. Annotates a run with cache-friendly metadata for logs / future headers.
2. Provides a local stand-in for "server compaction" (calls the same fold
   path the Context Manager already uses) when no vendor endpoint exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class CachePolicy:
    """Hints for APIs that support automatic prompt caching."""

    enabled: bool = True
    layout: str = "prefix_stable_suffix_variable"
    # Optional vendor fields (passed through extra_body when supported)
    prompt_cache_key: str | None = None
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        bits = [
            f"layout={self.layout}",
            f"enabled={self.enabled}",
        ]
        if self.prompt_cache_key:
            bits.append(f"cache_key={self.prompt_cache_key}")
        return "cache_policy(" + ", ".join(bits) + ")"

    def openai_extra_body(self) -> dict[str, Any] | None:
        """Best-effort extras for gateways that honor cache hints.

        DeepSeek's public API may ignore unknown fields; attaching them is
        harmless. Real KV-cache hit rates still depend on the provider.
        """
        if not self.enabled:
            return None
        body: dict[str, Any] = {
            # Common experimental / gateway fields — ignored if unsupported
            "prompt_cache_key": self.prompt_cache_key or self.layout,
        }
        return body


def build_cache_policy(*, workdir: str, model: str) -> CachePolicy:
    key = f"{model}:{workdir}"
    return CachePolicy(
        enabled=True,
        prompt_cache_key=key[:120],
        notes=[
            "Keep system prompt bytes fixed within a run.",
            "Append working memory as a variable suffix.",
            "Server-side compaction is not assumed; local fold is the fallback.",
        ],
    )


def local_server_compaction_fallback(
    prepare_fn: Callable[[], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """When the vendor has no compaction endpoint, run local prepare/fold."""
    return prepare_fn()
