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
    approval: ApprovalMode = ApprovalMode.AUTO
    transcript_dir: Path | None = None

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

        steps = max_steps if max_steps is not None else int(os.getenv("MAX_STEPS", "20"))
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

        mode = parse_approval_mode(approval or os.getenv("APPROVAL") or "auto")

        tdir_raw = transcript_dir if transcript_dir is not None else os.getenv("TRANSCRIPT_DIR")
        if tdir_raw is None or str(tdir_raw).strip() == "":
            tdir: Path | None = Path("transcripts").resolve()
        elif str(tdir_raw).strip().lower() in {"off", "none", "false", "0"}:
            tdir = None
        else:
            tdir = Path(tdir_raw).expanduser().resolve()

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=resolved_model,
            workdir=wd,
            max_steps=steps,
            max_tool_output_chars=max_out,
            max_messages=keep,
            approval=mode,
            transcript_dir=tdir,
        )
