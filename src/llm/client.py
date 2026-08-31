"""OpenAI-compatible LLM client (DeepSeek and others)."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from src.agent.cache_policy import CachePolicy, build_cache_policy
from src.agent.retry_policy import call_with_transient_retry
from src.config import Config


class LLMClient:
    """Thin wrapper around chat.completions — no agent logic here."""

    def __init__(self, config: Config, *, cache_policy: CachePolicy | None = None) -> None:
        self.config = config
        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url)
        self.cache_policy = cache_policy or build_cache_policy(
            workdir=str(config.workdir),
            model=config.model,
        )
        self.transient_retries = 0
        self.transient_recoveries = 0

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
        # Vendor-dependent prompt-cache hint (ignored if unsupported)
        extra = self.cache_policy.openai_extra_body()
        if extra:
            kwargs["extra_body"] = extra

        # E3: transient API errors get limited auto-retry; strategy/format do not.
        def _once() -> Any:
            return self._client.chat.completions.create(**kwargs)

        result, report = call_with_transient_retry(_once)
        if report.attempts > 1:
            self.transient_retries += report.attempts - 1
        if report.recovered:
            self.transient_recoveries += 1
        return result
