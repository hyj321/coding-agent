"""OpenAI-compatible LLM client (DeepSeek and others)."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from src.config import Config


class LLMClient:
    """Thin wrapper around chat.completions — no agent logic here."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return self._client.chat.completions.create(**kwargs)
