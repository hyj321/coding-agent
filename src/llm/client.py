"""OpenAI-compatible LLM client (DeepSeek and others)."""

from __future__ import annotations

import os
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI

from src.agent.cache_policy import CachePolicy, build_cache_policy
from src.agent.retry_policy import call_with_transient_retry
from src.config import Config


def _llm_timeout_sec() -> float:
    """HTTP timeout for provider calls (seconds). Override with LLM_TIMEOUT_SEC."""
    raw = (os.getenv("LLM_TIMEOUT_SEC") or "120").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 120.0
    return max(30.0, min(value, 600.0))


def _clarify_provider_error(exc: BaseException) -> BaseException:
    """Rewrite opaque connection failures into actionable messages."""
    name = type(exc).__name__
    text = str(exc) or ""
    lower = text.lower()
    # Browsers / some transports surface plain "network error" / TypeError.
    if isinstance(exc, (APIConnectionError, APITimeoutError)) or (
        "network error" in lower
        or "connection" in lower
        or "timed out" in lower
        or "timeout" in lower
    ):
        return RuntimeError(
            f"{name}: {text or 'network error'}. "
            "Provider unreachable or timed out (check BASE_URL, API key, "
            "proxy/VPN, and LLM_TIMEOUT_SEC). Transient — retry or shorten the task."
        )
    return exc


class LLMClient:
    """Thin wrapper around chat.completions — no agent logic here."""

    def __init__(self, config: Config, *, cache_policy: CachePolicy | None = None) -> None:
        self.config = config
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=_llm_timeout_sec(),
        )
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
            try:
                return self._client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 — clarify then re-raise for retry
                raise _clarify_provider_error(exc) from exc

        result, report = call_with_transient_retry(_once)
        if report.attempts > 1:
            self.transient_retries += report.attempts - 1
        if report.recovered:
            self.transient_recoveries += 1
        return result
