"""Action dedup + consecutive-call / error-streak guards (dispatch-boundary).

Detects pathological repeats without relying on the model to obey prompt text.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


def _normalize_for_fingerprint(value: Any) -> Any:
    """Stable JSON-ish normalize: sort dict keys, strip strings lightly."""
    if isinstance(value, dict):
        return {
            str(k): _normalize_for_fingerprint(value[k])
            for k in sorted(value.keys(), key=lambda x: str(x))
        }
    if isinstance(value, list):
        return [_normalize_for_fingerprint(v) for v in value]
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def tool_call_fingerprint(name: str, args: dict[str, Any] | str) -> str:
    """Fingerprint for loop detection: tool name + normalized arguments."""
    if isinstance(args, str):
        try:
            parsed: Any = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            parsed = {"_raw": args.strip()}
    else:
        parsed = args
    if not isinstance(parsed, dict):
        parsed = {"_value": parsed}
    payload = {"name": name, "args": _normalize_for_fingerprint(parsed)}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass
class LoopGuard:
    """Detect repeated identical tool+args calls; same-step dedup; error nudges."""

    warn_after: int = 3
    stop_after: int = 5
    error_nudge_after: int = 2
    last_fp: str | None = None
    streak: int = 0
    last_error_fp: str | None = None
    error_streak: int = 0
    _step_cache: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(
        cls,
        *,
        warn_after: int | None = None,
        stop_after: int | None = None,
        error_nudge_after: int | None = None,
    ) -> LoopGuard:
        warn = warn_after if warn_after is not None else int(os.getenv("LOOP_WARN_AFTER", "3"))
        stop = stop_after if stop_after is not None else int(os.getenv("LOOP_STOP_AFTER", "5"))
        nudge = (
            error_nudge_after
            if error_nudge_after is not None
            else int(os.getenv("LOOP_ERROR_NUDGE_AFTER", "2"))
        )
        if warn < 1:
            raise ValueError("LOOP_WARN_AFTER must be >= 1")
        if stop < warn:
            raise ValueError("LOOP_STOP_AFTER must be >= LOOP_WARN_AFTER")
        if nudge < 1:
            raise ValueError("LOOP_ERROR_NUDGE_AFTER must be >= 1")
        return cls(warn_after=warn, stop_after=stop, error_nudge_after=nudge)

    def begin_step(self) -> None:
        """Clear same-step dedup cache at the start of each LLM tool-call batch."""
        self._step_cache.clear()

    def same_step_lookup(self, fingerprint: str) -> str | None:
        """Return cached result if this fingerprint already ran in the current step."""
        return self._step_cache.get(fingerprint)

    def same_step_store(self, fingerprint: str, result: str) -> None:
        self._step_cache[fingerprint] = result

    def observe(self, name: str, args: dict[str, Any] | str) -> tuple[int, str]:
        """Return (streak, fingerprint). Resets streak when fingerprint changes."""
        fp = tool_call_fingerprint(name, args)
        if fp == self.last_fp:
            self.streak += 1
        else:
            self.last_fp = fp
            self.streak = 1
        return self.streak, fp

    def record_outcome(self, fingerprint: str, *, ok: bool) -> int:
        """Track consecutive failures of the same fingerprint. Return error_streak."""
        if ok:
            self.last_error_fp = None
            self.error_streak = 0
            return 0
        if fingerprint == self.last_error_fp:
            self.error_streak += 1
        else:
            self.last_error_fp = fingerprint
            self.error_streak = 1
        return self.error_streak

    def warning_suffix(self, name: str, streak: int) -> str | None:
        if streak < self.warn_after:
            return None
        if streak >= self.stop_after:
            return (
                f"\n\n[loop_guard] STOP: identical tool call `{name}` repeated "
                f"{streak} times with the same arguments. Change approach or "
                f"give a final answer; do not retry the exact same call."
            )
        return (
            f"\n\n[loop_guard] WARNING: identical tool call `{name}` repeated "
            f"{streak} times with the same arguments. Do not call it again "
            f"unchanged—adjust arguments, try another tool, or finish."
        )

    def error_nudge_suffix(self, name: str, error_streak: int) -> str | None:
        """OpenHands-style nudge when the same call keeps failing (before hard stop)."""
        if error_streak < self.error_nudge_after:
            return None
        return (
            f"\n\n[loop_guard] ERROR_STREAK: `{name}` failed {error_streak} times "
            f"in a row with the same arguments. Repeating the exact same call "
            f"will not work — change arguments, try a different tool/strategy, "
            f"or ask the user."
        )

    @staticmethod
    def dedup_reuse_message(name: str, cached: str) -> str:
        return (
            f"[dedup] Same `{name}` call already ran in this step — reusing result "
            f"(not re-executed).\n\n{cached}"
        )
