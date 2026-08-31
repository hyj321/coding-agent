"""Tool protocol and registry.

Adding a tool later = implement a Tool + register it. The agent loop does not change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol, Iterable

RiskLevel = Literal["low", "medium", "high"]


class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]

    def run(self, arguments: dict[str, Any]) -> str:
        """Execute the tool; always return a string (including error text)."""


Handler = Callable[[dict[str, Any]], str]


def validate_tool_metadata(tool: FunctionTool) -> None:
    """S6: reject internally inconsistent tool annotations at registration."""
    if tool.is_readonly and tool.destructive:
        raise ValueError(
            f"Tool {tool.name!r}: is_readonly and destructive cannot both be true"
        )
    if tool.is_readonly and tool.open_world:
        raise ValueError(
            f"Tool {tool.name!r}: is_readonly and open_world cannot both be true"
        )
    if tool.name == "run_shell" and not (tool.destructive and tool.network and tool.open_world):
        raise ValueError(
            "Tool 'run_shell' must set destructive=True, network=True, open_world=True"
        )


@dataclass
class FunctionTool:
    """Concrete tool: OpenAI-style schema + local handler + risk metadata."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    risk_level: RiskLevel = "medium"
    is_readonly: bool = False
    # S6 / MCP-style hints — enforced by PermissionGate where applicable
    destructive: bool = False
    network: bool = False
    open_world: bool = False

    def __post_init__(self) -> None:
        validate_tool_metadata(self)

    def run(self, arguments: dict[str, Any]) -> str:
        try:
            return self.handler(arguments)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the model
            return f"Error running tool {self.name!r}: {type(exc).__name__}: {exc}"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, FunctionTool] = {}

    def register(self, tool: FunctionTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        validate_tool_metadata(tool)
        self._tools[tool.name] = tool

    def get(self, name: str) -> FunctionTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def openai_tools(self, names: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Export schemas for chat.completions `tools=` parameter.

        If ``names`` is given, only those tools are included (least privilege).
        """
        if names is None:
            selected = list(self._tools.values())
        else:
            allow = set(names)
            selected = [t for t in self._tools.values() if t.name in allow]
        out: list[dict[str, Any]] = []
        for tool in selected:
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
            )
        return out

    def dispatch(self, name: str, arguments: dict[str, Any] | str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool {name!r}. Available: {', '.join(self.names())}"
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as exc:
                return f"Error: invalid JSON arguments for {name!r}: {exc}"
        if not isinstance(arguments, dict):
            return f"Error: arguments for {name!r} must be a JSON object"
        return tool.run(arguments)
