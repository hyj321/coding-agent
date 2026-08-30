"""Lightweight Agent Skills: catalog (L1) + on-demand body load (L2).

Skill = reusable playbook for a *class* of tasks (not a Tool, not a Todo).
Layout (Anthropic-style progressive disclosure):

  skills/<name>/SKILL.md   # YAML frontmatter (name, description) + short body

L1: only name + description go into the system prompt.
L2: model calls load_skill(name), OR harness keyword-router preloads body.
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

# Lightweight keyword router (SkillRouter downscaled for ~3–5 skills).
# Each entry: (compiled pattern, weight). Higher = stronger match.
_ROUTING_KEYWORDS: dict[str, list[tuple[re.Pattern[str], int]]] = {
    "debugging": [
        (re.compile(r"\bbugs?\b", re.I), 3),
        (re.compile(r"\bfix(?:es|ing)?\b", re.I), 2),
        (re.compile(r"\btraceback\b", re.I), 4),
        (re.compile(r"\bexception\b", re.I), 3),
        (re.compile(r"\berror\b", re.I), 2),
        (re.compile(r"\bfail(?:ing|ed|ure)?\b", re.I), 3),
        (re.compile(r"\bbroken\b", re.I), 2),
        (re.compile(r"断言失败|测试失败|跑不通|修(?:复|bug)|报错|异常|崩(?:溃)?", re.I), 3),
        (re.compile(r"greeter|buggy", re.I), 2),
    ],
    "testing": [
        (re.compile(r"\bunit\s*tests?\b", re.I), 4),
        (re.compile(r"\badd(?:ing)?\s+tests?\b", re.I), 4),
        (re.compile(r"\bwrite\s+tests?\b", re.I), 4),
        (re.compile(r"\bcoverage\b", re.I), 3),
        (re.compile(r"\bpytest\b", re.I), 2),
        (re.compile(r"补测|加测试|写测试|测例|覆盖率|测试套件", re.I), 3),
        (re.compile(r"\btests?\b", re.I), 1),
    ],
    "refactoring": [
        (re.compile(r"\brefactor(?:ing)?\b", re.I), 4),
        (re.compile(r"\bclean\s*up\b", re.I), 2),
        (re.compile(r"\brestructure\b", re.I), 3),
        (re.compile(r"\brename\b", re.I), 2),
        (re.compile(r"重构|整理代码|抽(?:取|离)|重命名|不改行为", re.I), 3),
    ],
}


@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str
    path: Path


@dataclass(frozen=True)
class SkillSuggestion:
    name: str
    score: int
    matched: tuple[str, ...]


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
        "**before** broad exploration (same turn as first reads), then follow it "
        "with `todo_write`. Skip `load_skill` if that skill was already "
        "**Preloaded** into the user message by the harness.",
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


def score_task_for_skills(task: str) -> dict[str, SkillSuggestion]:
    """Score installed skills against the task text (keyword router)."""
    text = (task or "").strip()
    installed = {s.name for s in discover_skills()}
    out: dict[str, SkillSuggestion] = {}
    if not text:
        return out
    for name, patterns in _ROUTING_KEYWORDS.items():
        if name not in installed:
            continue
        score = 0
        matched: list[str] = []
        for pat, weight in patterns:
            m = pat.search(text)
            if m:
                score += weight
                matched.append(m.group(0))
        if score > 0:
            out[name] = SkillSuggestion(name=name, score=score, matched=tuple(matched))
    return out


def suggest_skills(
    task: str,
    *,
    min_score: int = 3,
    margin: int = 1,
    top_k: int = 1,
) -> list[SkillSuggestion]:
    """Pick Top-K skills when score clears threshold and beats runner-up.

    With ~3 skills, mutual exclusion in descriptions + this keyword gate is enough;
    no embedding / cross-encoder needed. Near-ties fall back to L1 catalog only.
    """
    scored = list(score_task_for_skills(task).values())
    if not scored:
        return []
    scored.sort(key=lambda s: (-s.score, s.name))
    top = scored[0]
    if top.score < min_score:
        return []
    second = scored[1].score if len(scored) > 1 else 0
    if second > 0 and top.score - second < margin:
        return []
    return scored[: max(1, top_k)]


def format_skill_preload(name: str, *, score: int, matched: tuple[str, ...] = ()) -> str:
    """User-message appendix when harness preloads a skill (saves a load_skill step)."""
    body = load_skill_body(name)
    if body.startswith("Error:"):
        return ""
    hits = ", ".join(matched[:6]) if matched else "(keyword)"
    return (
        f"## Preloaded Skill: {name}\n"
        f"(harness keyword match; score={score}; hits: {hits})\n"
        f"Follow this playbook. Do **not** call `load_skill(\"{name}\")` again "
        f"this turn — proceed with `todo_write` + tools.\n\n"
        f"{body}"
    )
