"""Lightweight Agent Skills: catalog (L1) + on-demand body load (L2).

Skill = reusable playbook for a *class* of tasks (not a Tool, not a Todo).
Layout (Anthropic-style progressive disclosure):

  skills/<name>/SKILL.md   # YAML frontmatter (name, description) + short body

L1: only name + description go into the system prompt.
L2: model calls load_skill(name) to pull the body when relevant.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z",
    re.DOTALL,
)


@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str
    path: Path


def default_skills_dir() -> Path:
    """Repo-root `skills/` (parent of `src/`), overridable via SKILLS_DIR."""
    env = (os.getenv("SKILLS_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "skills"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip("'\"")
    body = match.group(2).strip()
    return meta, body


def discover_skills(skills_dir: Path | None = None) -> list[SkillMeta]:
    root = skills_dir or default_skills_dir()
    if not root.is_dir():
        return []
    found: list[SkillMeta] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.is_file():
            continue
        try:
            raw = skill_file.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, _body = _parse_frontmatter(raw)
        name = (meta.get("name") or child.name).strip()
        description = (meta.get("description") or "").strip()
        if not description:
            description = f"Skill '{name}' (see SKILL.md for steps)."
        found.append(SkillMeta(name=name, description=description, path=skill_file))
    return found


def format_skills_catalog(skills: list[SkillMeta] | None = None) -> str:
    """L1 block for the system prompt — names + descriptions only."""
    skills = discover_skills() if skills is None else skills
    if not skills:
        return ""
    lines = [
        "## Available Skills",
        "Skills are short playbooks for classes of tasks (not atomic tools).",
        "When a skill matches the user task, call `load_skill` with its name "
        "**before** broad exploration, then follow its steps "
        "(usually instantiate them with `todo_write`).",
        "",
    ]
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
    return "\n".join(lines)


def load_skill_body(name: str, skills_dir: Path | None = None) -> str:
    """L2: return skill body (without frontmatter), or an error string."""
    name = (name or "").strip()
    if not name:
        return "Error: load_skill requires 'name'"
    skills = discover_skills(skills_dir)
    by_name = {s.name: s for s in skills}
    skill = by_name.get(name)
    if skill is None:
        available = ", ".join(sorted(by_name)) or "(none)"
        return f"Error: unknown skill {name!r}. Available: {available}"
    try:
        raw = skill.path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Error: cannot read skill {name!r}: {exc}"
    _meta, body = _parse_frontmatter(raw)
    if not body:
        return f"Error: skill {name!r} has empty body"
    return f"# Skill: {skill.name}\n\n{body}"
