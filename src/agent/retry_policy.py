"""Staged retry policy: same failure strategy → escalate → stop.

Harness only injects constraints and counts; it does not auto-re-run tools.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any


_ERROR_CLASS = re.compile(
    r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception|Warning))\b"
)
_TEST_NODE = re.compile(r"(?m)^(?:FAILED|ERROR)\s+(\S+::\S+)")
_GENERIC_ERROR = re.compile(r"(?i)^Error:\s*(.{0,80})")


def extract_error_class(result: str) -> str:
    """Best-effort error class / test node for failure_key."""
    text = result or ""
    node = _TEST_NODE.search(text)
    if node:
        return node.group(1)[:80]
    cls = _ERROR_CLASS.search(text)
    if cls:
        return cls.group(1)
    gen = _GENERIC_ERROR.search(text.strip())
    if gen:
        snippet = re.sub(r"\s+", " ", gen.group(1)).strip()
        return (snippet[:40] or "Error").replace("|", "/")
    if re.search(r"(?m)^(FAILED|ERROR)\b", text):
        return "FAILED"
    if re.search(r"exit_code:\s*[1-9]", text):
        return "exit_nonzero"
    if text.strip().startswith("Error"):
        return "Error"
    return "fail"


def make_failure_key(
    tool_name: str,
    args: dict[str, Any] | str | None,
    result: str,
) -> str:
    """failure_key ≈ tool | target file/cmd-kind | error class."""
    path = "-"
    if isinstance(args, dict):
        raw_path = args.get("path")
        if isinstance(raw_path, str) and raw_path.strip():
            path = raw_path.replace("\\", "/").strip()[:120]
        elif tool_name == "run_shell":
            cmd = str(args.get("command") or "").strip().lower()
            if "pytest" in cmd or "py.test" in cmd:
                path = "pytest"
            elif "unittest" in cmd:
                path = "unittest"
            elif cmd:
                path = "shell:" + re.sub(r"\s+", " ", cmd)[:60]
        elif tool_name == "run_tests":
            raw_t = args.get("target")
            path = (
                str(raw_t).replace("\\", "/").strip()[:120]
                if isinstance(raw_t, str) and raw_t.strip()
                else "run_tests"
            )
    err = extract_error_class(result)
    return f"{tool_name}|{path}|{err}"


@dataclass
class FailedStrategy:
    key: str
    tool: str
    path: str
    error_class: str
    count: int = 1
    last_error: str = ""
    stage: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "tool": self.tool,
            "path": self.path,
            "error_class": self.error_class,
            "count": self.count,
            "last_error": self.last_error,
            "stage": self.stage,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailedStrategy:
        return cls(
            key=str(data.get("key") or ""),
            tool=str(data.get("tool") or ""),
            path=str(data.get("path") or "-"),
            error_class=str(data.get("error_class") or "fail"),
            count=int(data.get("count") or 1),
            last_error=str(data.get("last_error") or "")[:220],
            stage=int(data.get("stage") or 1),
        )


@dataclass
class RetryDecision:
    key: str
    stage: int
    count: int
    should_stop: bool
    suffix: str | None
    strategy: FailedStrategy


@dataclass
class RetryPolicy:
    """Track per-strategy failure counts; exhausted strategies hard-BLOCK at dispatch.

    After ``max_failures`` for a failure_key, the tool-call fingerprint is banned.
    The run does **not** stop on that failure alone — the model may switch strategy.
    A later call with the same fingerprint is blocked (no handler) and should stop
    the run with ``retry_exhausted`` (see agent loop).
    """

    max_failures: int = 3
    by_key: dict[str, FailedStrategy] = field(default_factory=dict)
    last_key: str | None = None
    last_stage: int = 0
    blocked_fingerprints: set[str] = field(default_factory=set)
    block_hits: int = 0

    @classmethod
    def from_env(cls, *, max_failures: int | None = None) -> RetryPolicy:
        n = (
            max_failures
            if max_failures is not None
            else int(os.getenv("RETRY_MAX_FAILURES", "3"))
        )
        if n < 2:
            raise ValueError("RETRY_MAX_FAILURES must be >= 2")
        return cls(max_failures=n)

    def is_blocked(self, fingerprint: str) -> bool:
        return bool(fingerprint) and fingerprint in self.blocked_fingerprints

    def ban_fingerprint(self, fingerprint: str) -> None:
        if fingerprint:
            self.blocked_fingerprints.add(fingerprint)

    def blocked_tool_message(self, name: str) -> str:
        """Dispatch-boundary hard block (Strands-style cancel_tool). Must start with Error:."""
        banned = self.banned_strategies_text()
        self.block_hits += 1
        return (
            f"Error: BLOCKED: strategy exhausted for tool `{name}`; "
            f"change args or tool. Do not retry the same call.\n"
            f"Banned strategies:\n{banned}"
        )

    def record_failure(
        self,
        *,
        tool_name: str,
        args: dict[str, Any] | str | None,
        result: str,
    ) -> RetryDecision:
        key = make_failure_key(tool_name, args, result)
        parts = key.split("|", 2)
        path = parts[1] if len(parts) > 1 else "-"
        err_class = parts[2] if len(parts) > 2 else extract_error_class(result)
        preview = " ".join((result or "").strip().split())
        if len(preview) > 180:
            preview = preview[:177] + "…"

        existing = self.by_key.get(key)
        if existing is None:
            existing = FailedStrategy(
                key=key,
                tool=tool_name,
                path=path,
                error_class=err_class,
                count=1,
                last_error=preview,
                stage=1,
            )
            self.by_key[key] = existing
        else:
            existing.count += 1
            existing.last_error = preview
            existing.stage = min(existing.count, self.max_failures)

        stage = existing.stage
        self.last_key = key
        self.last_stage = stage
        should_stop = existing.count >= self.max_failures
        # should_stop means: ban fingerprint + soft STOP suffix; hard stop is on
        # the next same-fingerprint dispatch (BLOCK), not on this failure alone.
        return RetryDecision(
            key=key,
            stage=stage,
            count=existing.count,
            should_stop=should_stop,
            suffix=self._suffix(existing, should_stop=should_stop),
            strategy=existing,
        )

    def _suffix(self, strat: FailedStrategy, *, should_stop: bool) -> str:
        banned = self.banned_strategies_text()
        if should_stop:
            return (
                f"\n\n[retry_policy] STOP stage={strat.stage}/{self.max_failures}: "
                f"strategy `{strat.key}` failed {strat.count} times. "
                f"This fingerprint is now BLOCKED at dispatch — you MUST change "
                f"args or tool (do not retry the same call). Ask the user or give "
                f"a final status report if stuck.\nBanned strategies:\n{banned}"
            )
        if strat.stage >= 2:
            return (
                f"\n\n[retry_policy] stage={strat.stage}/{self.max_failures}: "
                f"strategy `{strat.key}` failed again. You MUST change approach "
                f"(e.g. rewrite the whole function, edit a different region, "
                f"re-read tests, or ask the user). Do NOT reuse this strategy.\n"
                f"Banned strategies:\n{banned}"
            )
        return (
            f"\n\n[retry_policy] stage=1/{self.max_failures}: "
            f"recorded failure for `{strat.key}`. "
            f"You may adjust arguments once; on the next identical failure you "
            f"must change strategy."
        )

    def banned_strategies_text(self) -> str:
        if not self.by_key:
            return "(none)"
        lines: list[str] = []
        for s in sorted(self.by_key.values(), key=lambda x: (-x.count, x.key)):
            lines.append(
                f"- [{s.stage}/{self.max_failures}] {s.key} (count={s.count})"
            )
        return "\n".join(lines[:8])

    def render_block(self) -> str:
        if not self.by_key:
            return ""
        lines = [
            "### Failed strategies (do not repeat)",
            self.banned_strategies_text(),
        ]
        if self.last_stage:
            lines.append(f"Retry stage (last): {self.last_stage}/{self.max_failures}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_failures": self.max_failures,
            "last_key": self.last_key,
            "last_stage": self.last_stage,
            "failed": [s.to_dict() for s in self.by_key.values()],
            "blocked_fingerprints": sorted(self.blocked_fingerprints)[:40],
            "block_hits": self.block_hits,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RetryPolicy:
        if not isinstance(data, dict):
            return cls.from_env()
        max_f = int(data.get("max_failures") or 3)
        policy = cls(max_failures=max(2, max_f))
        policy.last_key = str(data["last_key"]) if data.get("last_key") else None
        policy.last_stage = int(data.get("last_stage") or 0)
        failed = data.get("failed")
        if isinstance(failed, list):
            for item in failed[-20:]:
                if isinstance(item, dict) and item.get("key"):
                    fs = FailedStrategy.from_dict(item)
                    policy.by_key[fs.key] = fs
                    # Re-ban fingerprints for exhausted strategies if caller also
                    # persisted blocked_fingerprints; otherwise keys alone are soft.
        blocked = data.get("blocked_fingerprints")
        if isinstance(blocked, list):
            for fp in blocked[:40]:
                if isinstance(fp, str) and fp.strip():
                    policy.blocked_fingerprints.add(fp.strip())
        policy.block_hits = int(data.get("block_hits") or 0)
        return policy
