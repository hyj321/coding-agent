"""Action dedup + consecutive-call / cycle / stagnation / error-streak guards.

Detects pathological repeats without relying on the model to obey prompt text.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Literal


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


def observation_fingerprint(name: str, result: str, *, max_chars: int = 2000) -> str:
    """Hash of tool name + whitespace-normalized observation body (Dec-B stagnation)."""
    text = " ".join((result or "").split())
    if len(text) > max_chars:
        text = text[:max_chars]
    raw = f"{name}|{text}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:32]


CycleLevel = Literal["warn", "stop"]


@dataclass(frozen=True)
class CycleHit:
    """Alternating / periodic fingerprint pattern (OpenHands-style ping-pong)."""

    level: CycleLevel
    period: int
    repeats: int
    pattern: tuple[str, ...]


@dataclass
class LoopGuard:
    """Detect repeated identical tool+args calls; A↔B cycles; obs stagnation; dedup."""

    warn_after: int = 3
    stop_after: int = 5
    error_nudge_after: int = 2
    # Cycle: pattern length 2..cycle_max_period; warn/stop by repeat count of that pattern
    cycle_warn_repeats: int = 2
    cycle_stop_repeats: int = 3
    cycle_max_period: int = 4
    # Stagnation: same observation hash streak (0 stop = warn-only, Dec-B default)
    stagnation_warn_after: int = 3
    stagnation_stop_after: int = 0
    last_fp: str | None = None
    streak: int = 0
    last_error_fp: str | None = None
    error_streak: int = 0
    last_obs_fp: str | None = None
    obs_streak: int = 0
    _step_cache: dict[str, str] = field(default_factory=dict)
    _fp_history: list[str] = field(default_factory=list)
    last_cycle: CycleHit | None = None

    @classmethod
    def from_env(
        cls,
        *,
        warn_after: int | None = None,
        stop_after: int | None = None,
        error_nudge_after: int | None = None,
        cycle_warn_repeats: int | None = None,
        cycle_stop_repeats: int | None = None,
        cycle_max_period: int | None = None,
        stagnation_warn_after: int | None = None,
        stagnation_stop_after: int | None = None,
    ) -> LoopGuard:
        warn = warn_after if warn_after is not None else int(os.getenv("LOOP_WARN_AFTER", "3"))
        stop = stop_after if stop_after is not None else int(os.getenv("LOOP_STOP_AFTER", "5"))
        nudge = (
            error_nudge_after
            if error_nudge_after is not None
            else int(os.getenv("LOOP_ERROR_NUDGE_AFTER", "2"))
        )
        c_warn = (
            cycle_warn_repeats
            if cycle_warn_repeats is not None
            else int(os.getenv("LOOP_CYCLE_WARN_REPEATS", "2"))
        )
        c_stop = (
            cycle_stop_repeats
            if cycle_stop_repeats is not None
            else int(os.getenv("LOOP_CYCLE_STOP_REPEATS", "3"))
        )
        c_period = (
            cycle_max_period
            if cycle_max_period is not None
            else int(os.getenv("LOOP_CYCLE_MAX_PERIOD", "4"))
        )
        s_warn = (
            stagnation_warn_after
            if stagnation_warn_after is not None
            else int(os.getenv("LOOP_STAGNATION_WARN_AFTER", "3"))
        )
        s_stop = (
            stagnation_stop_after
            if stagnation_stop_after is not None
            else int(os.getenv("LOOP_STAGNATION_STOP_AFTER", "0"))
        )
        if warn < 1:
            raise ValueError("LOOP_WARN_AFTER must be >= 1")
        if stop < warn:
            raise ValueError("LOOP_STOP_AFTER must be >= LOOP_WARN_AFTER")
        if nudge < 1:
            raise ValueError("LOOP_ERROR_NUDGE_AFTER must be >= 1")
        if c_warn < 2:
            raise ValueError("LOOP_CYCLE_WARN_REPEATS must be >= 2")
        if c_stop < c_warn:
            raise ValueError("LOOP_CYCLE_STOP_REPEATS must be >= LOOP_CYCLE_WARN_REPEATS")
        if c_period < 2 or c_period > 8:
            raise ValueError("LOOP_CYCLE_MAX_PERIOD must be in 2..8")
        if s_warn < 2:
            raise ValueError("LOOP_STAGNATION_WARN_AFTER must be >= 2")
        if s_stop < 0:
            raise ValueError("LOOP_STAGNATION_STOP_AFTER must be >= 0 (0=disabled)")
        if s_stop > 0 and s_stop < s_warn:
            raise ValueError(
                "LOOP_STAGNATION_STOP_AFTER must be 0 or >= LOOP_STAGNATION_WARN_AFTER"
            )
        return cls(
            warn_after=warn,
            stop_after=stop,
            error_nudge_after=nudge,
            cycle_warn_repeats=c_warn,
            cycle_stop_repeats=c_stop,
            cycle_max_period=c_period,
            stagnation_warn_after=s_warn,
            stagnation_stop_after=s_stop,
        )

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
        self._fp_history.append(fp)
        # Cap history: enough for max period × stop repeats (+ a little)
        cap = max(32, self.cycle_max_period * self.cycle_stop_repeats + 8)
        if len(self._fp_history) > cap:
            self._fp_history = self._fp_history[-cap:]
        self.last_cycle = self._detect_cycle()
        return self.streak, fp

    def record_observation(self, name: str, result: str) -> int:
        """Track consecutive identical observation hashes. Return obs_streak."""
        oh = observation_fingerprint(name, result)
        if oh == self.last_obs_fp:
            self.obs_streak += 1
        else:
            self.last_obs_fp = oh
            self.obs_streak = 1
        return self.obs_streak

    def stagnation_suffix(self, name: str, obs_streak: int | None = None) -> str | None:
        """Warn (or STOP if configured) when observations do not change."""
        n = self.obs_streak if obs_streak is None else obs_streak
        if n < self.stagnation_warn_after:
            return None
        if self.stagnation_stop_after > 0 and n >= self.stagnation_stop_after:
            return (
                f"\n\n[loop_guard] STAGNATION_STOP: tool `{name}` produced the same "
                f"observation {n} times in a row (no new information). Change approach "
                f"or give a final answer — do not keep calling with no progress."
            )
        return (
            f"\n\n[loop_guard] STAGNATION_WARN: tool `{name}` produced the same "
            f"observation {n} times in a row. Progress is stalled — change arguments, "
            f"try another tool, or finish with evidence."
        )

    def _detect_cycle(self) -> CycleHit | None:
        """Find repeating period-2..N pattern at the end of fingerprint history.

        Exact streaks (AAAA) are ignored here — handled by ``streak``.
        """
        hist = self._fp_history
        best: CycleHit | None = None
        for period in range(2, self.cycle_max_period + 1):
            need_warn = period * self.cycle_warn_repeats
            if len(hist) < need_warn:
                continue
            pattern = tuple(hist[-period:])
            if len(set(pattern)) < 2:
                continue
            repeats = 1
            pos = len(hist) - period
            while pos >= period:
                prev = tuple(hist[pos - period : pos])
                if prev != pattern:
                    break
                repeats += 1
                pos -= period
            if repeats < self.cycle_warn_repeats:
                continue
            level: CycleLevel = (
                "stop" if repeats >= self.cycle_stop_repeats else "warn"
            )
            hit = CycleHit(
                level=level, period=period, repeats=repeats, pattern=pattern
            )
            if best is None:
                best = hit
            elif hit.level == "stop" and best.level != "stop":
                best = hit
            elif hit.level == best.level and hit.repeats > best.repeats:
                best = hit
        return best

    def cycle_status(self) -> CycleHit | None:
        """Latest cycle verdict after ``observe`` (or None)."""
        return self.last_cycle

    def cycle_suffix(self, hit: CycleHit | None = None) -> str | None:
        hit = hit if hit is not None else self.last_cycle
        if hit is None:
            return None
        # Short labels for log/prompt (full fingerprints are huge)
        labels = [f"p{i}" for i in range(hit.period)]
        pattern_txt = " <-> ".join(labels)
        if hit.level == "stop":
            return (
                f"\n\n[loop_guard] CYCLE_STOP: alternating tool pattern "
                f"(period={hit.period}, repeats={hit.repeats}: {pattern_txt}) "
                f"detected. Stop ping-ponging — change approach or give a final "
                f"answer; do not alternate the same two calls."
            )
        return (
            f"\n\n[loop_guard] CYCLE_WARN: alternating tool pattern "
            f"(period={hit.period}, repeats={hit.repeats}: {pattern_txt}). "
            f"Break the cycle — pick a different tool/args or finish."
        )

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
