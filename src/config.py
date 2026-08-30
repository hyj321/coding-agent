"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from src.agent.permissions import ApprovalMode, parse_approval_mode


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str
    workdir: Path
    max_steps: int
    max_tool_output_chars: int = 8000
    max_messages: int = 40
    context_token_budget: int = 32000
    # Cost-A: cumulative task token hard cap (0 = disabled)
    max_task_tokens: int = 0
    task_token_output_reserve: int = 512
    approval: ApprovalMode = ApprovalMode.AUTO
    transcript_dir: Path | None = None
    loop_warn_after: int = 3
    loop_stop_after: int = 5
    loop_error_nudge_after: int = 2
    retry_max_failures: int = 3
    final_nudge_mutating_limit: int = 2
    # Safety P1
    tool_visibility: str = "auto"  # auto | off
    completion_mode: str = "evidence"  # evidence | trust_model
    evidence_nudge_max: int = 2
    # Ver-B: block | warn | off — only-test mutations + green tests
    fake_green_mode: str = "block"
    # Sec-A: high | deny | allow — pip/npm/curl risk policy
    network_policy: str = "high"
    deny_high: bool = False

    @classmethod
    def from_env(
        cls,
        *,
        workdir: str | Path | None = None,
        model: str | None = None,
        max_steps: int | None = None,
        approval: str | None = None,
        transcript_dir: str | Path | None = None,
        max_messages: int | None = None,
        context_token_budget: int | None = None,
        max_task_tokens: int | None = None,
        task_token_output_reserve: int | None = None,
        loop_warn_after: int | None = None,
        loop_stop_after: int | None = None,
        loop_error_nudge_after: int | None = None,
        retry_max_failures: int | None = None,
        final_nudge_mutating_limit: int | None = None,
    ) -> Config:
        load_dotenv()

        api_key = (
            os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ).strip()
        if not api_key:
            raise ValueError(
                "Missing API key. Set DEEPSEEK_API_KEY (or API_KEY) in the environment "
                "or a local .env file. See .env.example."
            )

        base_url = (
            os.getenv("BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        ).rstrip("/")

        resolved_model = model or os.getenv("MODEL") or "deepseek-v4-flash"

        wd = Path(workdir or os.getenv("WORKDIR") or ".").expanduser().resolve()
        if not wd.is_dir():
            raise ValueError(f"WORKDIR is not a directory: {wd}")

        steps = max_steps if max_steps is not None else int(os.getenv("MAX_STEPS", "30"))
        if steps < 1:
            raise ValueError("MAX_STEPS must be >= 1")

        max_out = int(os.getenv("MAX_TOOL_OUTPUT_CHARS", "8000"))
        keep = (
            max_messages
            if max_messages is not None
            else int(os.getenv("MAX_MESSAGES", "40"))
        )
        if keep < 4:
            raise ValueError("MAX_MESSAGES must be >= 4")

        token_budget = (
            context_token_budget
            if context_token_budget is not None
            else int(os.getenv("CONTEXT_TOKEN_BUDGET", "32000"))
        )
        if token_budget < 1500:
            raise ValueError("CONTEXT_TOKEN_BUDGET must be >= 1500")

        task_tok = (
            max_task_tokens
            if max_task_tokens is not None
            else int(os.getenv("MAX_TASK_TOKENS", "0") or "0")
        )
        if task_tok < 0:
            raise ValueError("MAX_TASK_TOKENS must be >= 0 (0 disables the hard gate)")
        out_reserve = (
            task_token_output_reserve
            if task_token_output_reserve is not None
            else int(os.getenv("TASK_TOKEN_OUTPUT_RESERVE", "512") or "512")
        )
        if out_reserve < 0:
            raise ValueError("TASK_TOKEN_OUTPUT_RESERVE must be >= 0")

        mode = parse_approval_mode(approval or os.getenv("APPROVAL") or "auto")

        tdir_raw = transcript_dir if transcript_dir is not None else os.getenv("TRANSCRIPT_DIR")
        if tdir_raw is None or str(tdir_raw).strip() == "":
            tdir: Path | None = Path("transcripts").resolve()
        elif str(tdir_raw).strip().lower() in {"off", "none", "false", "0"}:
            tdir = None
        else:
            tdir = Path(tdir_raw).expanduser().resolve()

        warn = (
            loop_warn_after
            if loop_warn_after is not None
            else int(os.getenv("LOOP_WARN_AFTER", "3"))
        )
        stop = (
            loop_stop_after
            if loop_stop_after is not None
            else int(os.getenv("LOOP_STOP_AFTER", "5"))
        )
        nudge = (
            loop_error_nudge_after
            if loop_error_nudge_after is not None
            else int(os.getenv("LOOP_ERROR_NUDGE_AFTER", "2"))
        )
        if warn < 1:
            raise ValueError("LOOP_WARN_AFTER must be >= 1")
        if stop < warn:
            raise ValueError("LOOP_STOP_AFTER must be >= LOOP_WARN_AFTER")
        if nudge < 1:
            raise ValueError("LOOP_ERROR_NUDGE_AFTER must be >= 1")

        retry_max = (
            retry_max_failures
            if retry_max_failures is not None
            else int(os.getenv("RETRY_MAX_FAILURES", "3"))
        )
        if retry_max < 2:
            raise ValueError("RETRY_MAX_FAILURES must be >= 2")
        nudge_limit = (
            final_nudge_mutating_limit
            if final_nudge_mutating_limit is not None
            else int(os.getenv("FINAL_NUDGE_MUTATING_LIMIT", "2"))
        )
        if nudge_limit < 1:
            raise ValueError("FINAL_NUDGE_MUTATING_LIMIT must be >= 1")

        visibility = (os.getenv("TOOL_VISIBILITY") or "auto").strip().lower()
        if visibility not in {"auto", "off", "full", "none"}:
            visibility = "auto"
        if visibility in {"full", "none"}:
            visibility = "off"

        completion = (os.getenv("COMPLETION_MODE") or "evidence").strip().lower()
        if completion not in {"evidence", "trust_model", "off"}:
            completion = "evidence"

        evidence_max = int(os.getenv("EVIDENCE_NUDGE_MAX", "2"))
        if evidence_max < 1:
            raise ValueError("EVIDENCE_NUDGE_MAX must be >= 1")

        fake_green = (os.getenv("FAKE_GREEN_MODE") or "block").strip().lower()
        if fake_green not in {"block", "warn", "off"}:
            fake_green = "block"

        network_policy = (os.getenv("NETWORK_POLICY") or "high").strip().lower()
        if network_policy not in {"high", "deny", "allow"}:
            network_policy = "high"

        deny_high_env = (os.getenv("DENY_HIGH") or "").strip().lower()
        deny_high = deny_high_env in {"1", "true", "yes", "on"}

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=resolved_model,
            workdir=wd,
            max_steps=steps,
            max_tool_output_chars=max_out,
            max_messages=keep,
            context_token_budget=token_budget,
            max_task_tokens=task_tok,
            task_token_output_reserve=out_reserve,
            approval=mode,
            transcript_dir=tdir,
            loop_warn_after=warn,
            loop_stop_after=stop,
            loop_error_nudge_after=nudge,
            retry_max_failures=retry_max,
            final_nudge_mutating_limit=nudge_limit,
            tool_visibility=visibility,
            completion_mode=completion,
            evidence_nudge_max=evidence_max,
            fake_green_mode=fake_green,
            network_policy=network_policy,
            deny_high=deny_high,
        )
