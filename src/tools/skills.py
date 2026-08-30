"""load_skill — fetch a Skill playbook body on demand (progressive disclosure L2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agent.skills import discover_skills, load_skill_body
from src.tools.base import FunctionTool, ToolRegistry


def register_skill_tools(
    registry: ToolRegistry,
    *,
    skills_dir: Path | None = None,
) -> None:
    def load_skill(args: dict[str, Any]) -> str:
        name = str(args.get("name") or "").strip()
        return load_skill_body(name, skills_dir=skills_dir)

    available = ", ".join(s.name for s in discover_skills(skills_dir)) or "(none installed)"
    registry.register(
        FunctionTool(
            name="load_skill",
            description=(
                "Load the full instructions for a named Skill playbook. "
                "Call this when an Available Skill matches the task "
                f"(installed: {available}). Returns step-by-step guidance; "
                "then follow it (todo_write + reads may share the same turn)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name, e.g. 'debugging'.",
                    }
                },
                "required": ["name"],
            },
            handler=load_skill,
            risk_level="low",
            is_readonly=True,
        )
    )
