"""Agent loop: call model → dispatch tools → append results → repeat."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.llm.client import LLMClient
from src.tools.base import ToolRegistry

LogFn = Callable[[str], None]


def _default_log(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class AgentResult:
    final_text: str
    steps: int
    stopped_reason: str  # completed | max_steps | interrupted
    messages: list[dict[str, Any]] = field(default_factory=list)


def _message_to_dict(message: Any) -> dict[str, Any]:
    """Convert OpenAI SDK message object into a plain dict for history."""
    data: dict[str, Any] = {
        "role": message.role,
        "content": message.content,
    }
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        data["tool_calls"] = [
            {
                "id": tc.id,
                "type": getattr(tc, "type", "function") or "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in tool_calls
        ]
    return data


def _summarize_args(raw: str, limit: int = 120) -> str:
    raw = raw.replace("\n", "\\n")
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "..."


def _summarize_result(text: str, limit: int = 200) -> str:
    flat = text.replace("\n", "\\n")
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "..."


def run_agent(
    *,
    client: LLMClient,
    registry: ToolRegistry,
    system_prompt: str,
    user_task: str,
    max_steps: int,
    log: LogFn | None = None,
) -> AgentResult:
    """Core harness loop. Extensible: swap registry / client without changing this."""
    log = log or _default_log
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_task},
    ]
    tools = registry.openai_tools()

    log(f"[agent] model={client.config.model} max_steps={max_steps}")
    log(f"[agent] tools={', '.join(registry.names())}")
    log(f"[agent] task={user_task!r}")

    try:
        for step in range(1, max_steps + 1):
            log(f"\n=== step {step}/{max_steps} ===")
            response = client.chat(messages, tools=tools)
            choice = response.choices[0]
            message = choice.message
            assistant_dict = _message_to_dict(message)
            messages.append(assistant_dict)

            tool_calls = assistant_dict.get("tool_calls") or []
            if not tool_calls:
                final = (assistant_dict.get("content") or "").strip()
                log(f"[agent] final:\n{final}")
                return AgentResult(
                    final_text=final,
                    steps=step,
                    stopped_reason="completed",
                    messages=messages,
                )

            if assistant_dict.get("content"):
                log(f"[think] {assistant_dict['content']}")

            for tc in tool_calls:
                name = tc["function"]["name"]
                raw_args = tc["function"]["arguments"]
                log(f"[tool] {name}({_summarize_args(raw_args)})")
                result = registry.dispatch(name, raw_args)
                log(f"[result] {_summarize_result(result)}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    }
                )

        log(f"[agent] stopped: reached max_steps={max_steps}")
        return AgentResult(
            final_text=f"(stopped after {max_steps} steps without a final answer)",
            steps=max_steps,
            stopped_reason="max_steps",
            messages=messages,
        )
    except KeyboardInterrupt:
        log("\n[agent] interrupted by user")
        return AgentResult(
            final_text="(interrupted)",
            steps=0,
            stopped_reason="interrupted",
            messages=messages,
        )
