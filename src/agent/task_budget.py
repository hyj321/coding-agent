"""Task-level cumulative token budget (Cost-A sync gate + Cost-B awareness).

Hard-stop before the next LLM call when projected spend would exceed
``MAX_TASK_TOKENS``. Uses the same chars/4 estimate as ContextManager unless
the provider returns real ``usage`` on the response.

Cost-B adds: tool counts, cost_report, Current State budget line, and a
one-shot ≤20% remaining warn (no second model).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from src.agent.compress import estimate_messages_tokens, estimate_tokens

_WARN_MARKER = "[budget_warn]"


def _estimate_assistant_tokens(message: dict[str, Any] | Any) -> int:
    """Estimate completion tokens from an assistant message dict or SDK object."""
    if isinstance(message, dict):
        content = message.get("content") or ""
        total = estimate_tokens(str(content)) if content else 0
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            total += estimate_tokens(str(fn.get("name") or ""))
            total += estimate_tokens(str(fn.get("arguments") or ""))
        return max(1, total) if total else 1

    content = getattr(message, "content", None) or ""
    total = estimate_tokens(str(content)) if content else 0
    for tc in getattr(message, "tool_calls", None) or []:
        fn = getattr(tc, "function", None)
        if fn is None:
            continue
        total += estimate_tokens(str(getattr(fn, "name", "") or ""))
        total += estimate_tokens(str(getattr(fn, "arguments", "") or ""))
    return max(1, total) if total else 1


def _usage_from_response(response: Any) -> tuple[int, int] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    if prompt is None or completion is None:
        return None
    try:
        return int(prompt), int(completion)
    except (TypeError, ValueError):
        return None


def _fmt_tok(n: int | float | None) -> str:
    if n is None:
        return "—"
    v = float(n)
    if v >= 10000:
        return f"{v / 1000:.0f}k"
    if v >= 1000:
        return f"{v / 1000:.1f}k"
    return str(int(round(v)))


@dataclass
class TaskBudget:
    """Cumulative token envelope for one agent run.

    ``max_task_tokens <= 0`` disables the hard gate; counters / cost_report /
    step-based warn still work (observe-only).
    """

    max_task_tokens: int = 0
    output_reserve: int = 512
    warn_ratio: float = 0.20
    tokens_used: int = 0
    tokens_in_est: int = 0
    tokens_out_est: int = 0
    llm_calls: int = 0
    last_prompt_est: int = 0
    last_completion_est: int = 0
    peak_context_tokens: int = 0
    compress_events: int = 0
    stopped_kind: str | None = None
    warn_injected: bool = False
    tool_counts: dict[str, int] = field(default_factory=dict)
    _events: list[dict[str, Any]] = field(default_factory=list, repr=False)

    @classmethod
    def from_config(
        cls,
        *,
        max_task_tokens: int | None = None,
        output_reserve: int | None = None,
        warn_ratio: float | None = None,
    ) -> TaskBudget:
        if max_task_tokens is None:
            max_task_tokens = int(os.getenv("MAX_TASK_TOKENS", "0") or "0")
        if output_reserve is None:
            output_reserve = int(os.getenv("TASK_TOKEN_OUTPUT_RESERVE", "512") or "512")
        if warn_ratio is None:
            warn_ratio = float(os.getenv("TASK_BUDGET_WARN_RATIO", "0.20") or "0.20")
        if max_task_tokens < 0:
            raise ValueError("MAX_TASK_TOKENS must be >= 0 (0 disables)")
        if output_reserve < 0:
            raise ValueError("TASK_TOKEN_OUTPUT_RESERVE must be >= 0")
        if not (0.0 < warn_ratio < 1.0):
            raise ValueError("TASK_BUDGET_WARN_RATIO must be in (0, 1)")
        return cls(
            max_task_tokens=max_task_tokens,
            output_reserve=output_reserve,
            warn_ratio=warn_ratio,
        )

    @property
    def enabled(self) -> bool:
        return self.max_task_tokens > 0

    @property
    def remaining(self) -> int | None:
        if not self.enabled:
            return None
        return max(0, self.max_task_tokens - self.tokens_used)

    def used_pct(self) -> float | None:
        if not self.enabled or self.max_task_tokens <= 0:
            return None
        return min(100.0, 100.0 * self.tokens_used / self.max_task_tokens)

    def remaining_pct(self) -> float | None:
        pct = self.used_pct()
        if pct is None:
            return None
        return max(0.0, 100.0 - pct)

    def level(self, *, step: int = 0, max_steps: int = 0) -> str:
        """ok | warn | critical — for UI / Current State."""
        if self.enabled:
            rem = self.remaining_pct()
            if rem is not None:
                if rem <= 5 or (self.remaining is not None and self.remaining <= self.output_reserve):
                    return "critical"
                if rem <= self.warn_ratio * 100:
                    return "warn"
        if max_steps > 0 and step > 0:
            left = max_steps - step
            if left <= max(1, int(max_steps * 0.05)):
                return "critical"
            if left <= max(1, int(max_steps * self.warn_ratio)):
                return "warn"
        return "ok"

    def project_cost(self, prompt_tokens: int) -> int:
        """Worst-case tokens if we fire the next LLM call now."""
        return self.tokens_used + max(0, int(prompt_tokens)) + self.output_reserve

    def would_exceed(self, prompt_tokens: int) -> bool:
        if not self.enabled:
            return False
        return self.project_cost(prompt_tokens) > self.max_task_tokens

    def check_before_llm(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Return a deny decision dict if the next LLM call must not run."""
        prompt_est = estimate_messages_tokens(messages)
        self.last_prompt_est = prompt_est
        if not self.would_exceed(prompt_est):
            return None
        self.stopped_kind = "tokens"
        decision = {
            "allow": False,
            "budget_kind": "tokens",
            "prompt_est": prompt_est,
            "projected": self.project_cost(prompt_est),
            "tokens_used": self.tokens_used,
            "max_task_tokens": self.max_task_tokens,
            "remaining": self.remaining,
            "llm_calls": self.llm_calls,
        }
        self._events.append({"type": "budget_deny", **decision})
        return decision

    def record_llm_turn(
        self,
        *,
        messages: list[dict[str, Any]] | None = None,
        response: Any = None,
        assistant_message: dict[str, Any] | Any | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> dict[str, int]:
        """Accumulate usage after a successful LLM round-trip."""
        real = _usage_from_response(response) if response is not None else None
        if real is not None:
            pin, cout = real
        else:
            pin = (
                int(prompt_tokens)
                if prompt_tokens is not None
                else (
                    estimate_messages_tokens(messages)
                    if messages is not None
                    else self.last_prompt_est
                )
            )
            if completion_tokens is not None:
                cout = int(completion_tokens)
            elif assistant_message is not None:
                cout = _estimate_assistant_tokens(assistant_message)
            else:
                cout = self.output_reserve
        pin = max(0, pin)
        cout = max(0, cout)
        self.tokens_in_est += pin
        self.tokens_out_est += cout
        self.tokens_used += pin + cout
        self.llm_calls += 1
        self.last_prompt_est = pin
        self.last_completion_est = cout
        if pin > self.peak_context_tokens:
            self.peak_context_tokens = pin
        snap = {"prompt": pin, "completion": cout, "tokens_used": self.tokens_used}
        self._events.append({"type": "budget_record", **snap})
        return snap

    def record_tool(self, name: str) -> None:
        key = (name or "unknown").strip() or "unknown"
        self.tool_counts[key] = int(self.tool_counts.get(key) or 0) + 1

    def note_context_usage(self, used_tokens: int | None, *, compress_events: int | None = None) -> None:
        if used_tokens is not None:
            u = max(0, int(used_tokens))
            if u > self.peak_context_tokens:
                self.peak_context_tokens = u
        if compress_events is not None:
            self.compress_events = max(self.compress_events, int(compress_events))

    def format_line(self, *, step: int = 0, max_steps: int = 0) -> str:
        """One-liner for Current State / prompt injection."""
        steps_bit = f"steps {step}/{max_steps}" if max_steps > 0 else f"steps {step}"
        if self.enabled:
            rem = self.remaining if self.remaining is not None else 0
            used_pct = self.used_pct() or 0.0
            tok_bit = (
                f"tokens ≈ {_fmt_tok(self.tokens_used)}/{_fmt_tok(self.max_task_tokens)} "
                f"({used_pct:.0f}% used, rem≈{_fmt_tok(rem)})"
            )
        else:
            tok_bit = f"tokens≈{_fmt_tok(self.tokens_used)} (cap=off)"
        lvl = self.level(step=step, max_steps=max_steps)
        return f"Budget: {steps_bit} | {tok_bit} | level={lvl}"

    def maybe_warn_message(self, *, step: int, max_steps: int) -> str | None:
        """One-shot soft warn when remaining ≤ warn_ratio (tokens or steps)."""
        if self.warn_injected:
            return None
        reasons: list[str] = []
        if self.enabled:
            rem_pct = self.remaining_pct()
            if rem_pct is not None and rem_pct <= self.warn_ratio * 100:
                reasons.append(
                    f"task tokens remaining ≈{rem_pct:.0f}% "
                    f"({_fmt_tok(self.remaining)}/{_fmt_tok(self.max_task_tokens)})"
                )
        if max_steps > 0 and step > 0:
            left = max_steps - step
            if left <= max(1, int(max_steps * self.warn_ratio)):
                reasons.append(f"steps remaining {left}/{max_steps}")
        if not reasons:
            return None
        self.warn_injected = True
        why = "; ".join(reasons)
        return (
            f"{_WARN_MARKER} Remaining task budget is low ({why}). "
            "Prefer verification and finishing over new broad exploration; "
            "avoid full-file reads and redundant tool loops."
        )

    def stop_message(self, decision: dict[str, Any]) -> str:
        used = decision.get("tokens_used", self.tokens_used)
        cap = decision.get("max_task_tokens", self.max_task_tokens)
        projected = decision.get(
            "projected", self.project_cost(int(decision.get("prompt_est") or 0))
        )
        return (
            f"(stopped: task token budget exhausted — "
            f"used≈{used}/{cap} tok, next call projected≈{projected}; "
            f"budget_kind=tokens)"
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_task_tokens": self.max_task_tokens,
            "output_reserve": self.output_reserve,
            "warn_ratio": self.warn_ratio,
            "tokens_used": self.tokens_used,
            "tokens_in_est": self.tokens_in_est,
            "tokens_out_est": self.tokens_out_est,
            "tokens_total_est": self.tokens_used,
            "remaining": self.remaining,
            "used_pct": self.used_pct(),
            "remaining_pct": self.remaining_pct(),
            "llm_calls": self.llm_calls,
            "peak_context_tokens": self.peak_context_tokens,
            "compress_events": self.compress_events,
            "tool_counts": dict(sorted(self.tool_counts.items())),
            "budget_kind": self.stopped_kind,
            "warn_injected": self.warn_injected,
        }

    def cost_report(
        self,
        *,
        steps: int,
        max_steps: int,
        stopped_reason: str | None = None,
    ) -> dict[str, Any]:
        """Structured attribution for memory / transcript / Web FINAL."""
        tools = dict(sorted(self.tool_counts.items()))
        return {
            "steps": int(steps),
            "max_steps": int(max_steps),
            "llm_calls": self.llm_calls,
            "tokens_in_est": self.tokens_in_est,
            "tokens_out_est": self.tokens_out_est,
            "tokens_total_est": self.tokens_used,
            "peak_context_tokens": self.peak_context_tokens,
            "max_task_tokens": self.max_task_tokens if self.enabled else 0,
            "budget_enabled": self.enabled,
            "budget_kind": self.stopped_kind,
            "tool_counts": tools,
            "tool_calls_total": sum(tools.values()),
            "compress_events": self.compress_events,
            "level": self.level(step=steps, max_steps=max_steps),
            "stopped_reason": stopped_reason,
            "summary": self.format_summary_line(steps=steps, max_steps=max_steps),
        }

    def format_summary_line(self, *, steps: int, max_steps: int = 0) -> str:
        tools = self.tool_counts
        if tools:
            top = sorted(tools.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
            tool_bit = ", ".join(f"{n}×{c}" for n, c in top)
            if len(tools) > 4:
                tool_bit += ", …"
        else:
            tool_bit = "none"
        steps_bit = f"{steps}/{max_steps} steps" if max_steps else f"{steps} steps"
        return (
            f"{steps_bit} · ≈{_fmt_tok(self.tokens_used)} tok "
            f"(in {_fmt_tok(self.tokens_in_est)} / out {_fmt_tok(self.tokens_out_est)}) · "
            f"tools: {tool_bit}"
        )


WARN_MARKER = _WARN_MARKER
