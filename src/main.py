"""CLI entry: python -m src.main \"your task\"."""

from __future__ import annotations

import argparse
import sys

from src.agent.context import build_system_prompt
from src.agent.loop import run_agent
from src.agent.permissions import PermissionGate
from src.agent.transcript import save_transcript
from src.config import Config
from src.llm.client import LLMClient
from src.tools import build_default_registry


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="codeagent",
        description="Minimal extensible coding agent (DeepSeek tool calling + local tools).",
    )
    p.add_argument(
        "task",
        nargs="?",
        help="Programming task for the agent. If omitted, read from stdin / prompt.",
    )
    p.add_argument(
        "-w",
        "--workdir",
        default=None,
        help="Working directory sandbox root (default: WORKDIR env or .).",
    )
    p.add_argument(
        "-m",
        "--model",
        default=None,
        help="Model id (default: MODEL env or deepseek-v4-flash).",
    )
    p.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Max model/tool iterations (default: MAX_STEPS env or 20).",
    )
    p.add_argument(
        "--approval",
        choices=["auto", "ask", "never"],
        default=None,
        help="Tool approval policy: auto (default), ask (confirm mutating/risky), never (deny risky).",
    )
    p.add_argument(
        "--max-messages",
        type=int,
        default=None,
        help="Max messages kept in model context (default: MAX_MESSAGES env or 40).",
    )
    p.add_argument(
        "--context-budget",
        type=int,
        default=None,
        help="Approx token budget for Context Manager (default: CONTEXT_TOKEN_BUDGET or 32000).",
    )
    p.add_argument(
        "--max-task-tokens",
        type=int,
        default=None,
        help="Hard cumulative token cap for the whole run (default: MAX_TASK_TOKENS or 0=off).",
    )
    p.add_argument(
        "--transcript-dir",
        default=None,
        help="Directory for JSON transcripts (default: transcripts/). Use 'off' to disable.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = Config.from_env(
            workdir=args.workdir,
            model=args.model,
            max_steps=args.max_steps,
            approval=args.approval,
            transcript_dir=args.transcript_dir,
            max_messages=args.max_messages,
            context_token_budget=args.context_budget,
            max_task_tokens=args.max_task_tokens,
        )
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    task = args.task
    if not task:
        print("Enter task (end with empty line / Ctrl+Z on Windows then Enter):")
        lines = sys.stdin.read().strip()
        task = lines
    if not task:
        print("No task provided.", file=sys.stderr)
        return 2

    def cli_ask_user(question: str, call_id: str | None = None) -> str:
        _ = call_id
        print(f"\n[Agent asks]\n{question}\n")
        try:
            answer = input("Your answer> ").strip()
        except EOFError:
            return "Error: user did not answer (no stdin)"
        if not answer:
            return "Error: user replied with empty text"
        return f"User answer:\n{answer}"

    gate = PermissionGate(
        config.workdir,
        approval=config.approval,
        deny_high=bool(getattr(config, "deny_high", False)),
        network_policy=str(getattr(config, "network_policy", "high") or "high"),
        shell_mode=str(getattr(config, "shell_mode", "open") or "open"),
        shell_allowlist_prefixes=getattr(config, "shell_allowlist_prefixes", None),
    )
    registry = build_default_registry(
        gate,
        max_output_chars=config.max_tool_output_chars,
        transcript_dir=config.transcript_dir,
        ask_user_fn=cli_ask_user,
    )
    system_prompt = build_system_prompt(config.workdir, registry.names())
    client = LLMClient(config)

    print(f"[config] workdir={config.workdir}")
    print(f"[config] base_url={config.base_url} model={config.model}")
    print(
        f"[config] approval={config.approval.value} "
        f"network={config.network_policy} shell_mode={config.shell_mode} "
        f"max_messages={config.max_messages} "
        f"context_budget≈{config.context_token_budget} "
        f"max_task_tokens={config.max_task_tokens or 'off'}"
    )

    result = run_agent(
        client=client,
        registry=registry,
        system_prompt=system_prompt,
        user_task=task,
        max_steps=config.max_steps,
        gate=gate,
        max_messages=config.max_messages,
        context_token_budget=config.context_token_budget,
        transcript_dir=config.transcript_dir,
        max_task_tokens=config.max_task_tokens,
        ask_user_fn=cli_ask_user,
    )

    if config.transcript_dir is not None:
        path = save_transcript(
            config.transcript_dir,
            task=task,
            result=result,
            meta={
                "model": config.model,
                "workdir": str(config.workdir),
                "approval": config.approval.value,
            },
        )
        print(f"[transcript] {path}")

    print("\n========== RESULT ==========")
    print(f"stopped_reason: {result.stopped_reason}")
    print(f"steps: {result.steps}")
    print(result.final_text)
    return 0 if result.stopped_reason == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
