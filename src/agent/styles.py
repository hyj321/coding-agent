"""Style cards: reusable writing/coding style playbooks under workdir.

Layout (per workdir)::

  .agent/styles/<slug>.md   # YAML frontmatter + body rules

Cards are short, actionable rules (not full source documents). The agent
extracts them via save_style; the Web UI can list / edit / activate them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z",
    re.DOTALL,
)
_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_MAX_BODY_CHARS = 6000
_MAX_DESC_CHARS = 240


@dataclass(frozen=True)
class StyleCard:
    id: str
    name: str
    description: str
    body: str
    path: Path
    updated_at: str = ""
    kind: str = "writing"  # writing | code | mixed

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "body": self.body,
            "updated_at": self.updated_at,
            "kind": self.kind,
            "path": str(self.path).replace("\\", "/"),
        }


def normalize_style_kind(raw: str | None) -> str:
    k = (raw or "writing").strip().lower()
    if k in {"code", "coding", "source"}:
        return "code"
    if k in {"mixed", "both", "all"}:
        return "mixed"
    return "writing"


def styles_dir(workdir: Path) -> Path:
    return Path(workdir).resolve() / ".agent" / "styles"


def normalize_style_id(raw: str) -> str:
    text = (raw or "").strip().lower().replace(" ", "-")
    text = re.sub(r"[^a-z0-9_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text or not _SLUG_RE.match(text):
        raise ValueError(
            "style id must be 1–64 chars: letters, digits, _ or - "
            f"(got {raw!r})"
        )
    return text


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
    return meta, match.group(2).strip()


def _render_card_markdown(
    *,
    style_id: str,
    name: str,
    description: str,
    body: str,
    kind: str = "writing",
    updated_at: str | None = None,
) -> str:
    ts = updated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    desc = (description or "").strip()[:_MAX_DESC_CHARS]
    kind_n = normalize_style_kind(kind)
    body_clean = (body or "").strip()
    if len(body_clean) > _MAX_BODY_CHARS:
        body_clean = body_clean[:_MAX_BODY_CHARS] + "\n\n…[truncated]"
    return (
        f"---\n"
        f"id: {style_id}\n"
        f"name: {name}\n"
        f"kind: {kind_n}\n"
        f"description: {desc}\n"
        f"updated_at: {ts}\n"
        f"---\n\n"
        f"{body_clean}\n"
    )


def _card_from_file(path: Path) -> StyleCard | None:
    if not path.is_file() or path.suffix.lower() != ".md":
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _parse_frontmatter(raw)
    style_id = (meta.get("id") or path.stem).strip()
    try:
        style_id = normalize_style_id(style_id)
    except ValueError:
        style_id = path.stem
    name = (meta.get("name") or style_id).strip()
    description = (meta.get("description") or "").strip()
    updated_at = (meta.get("updated_at") or "").strip()
    kind = normalize_style_kind(meta.get("kind"))
    if not description:
        description = f"Style card '{name}'."
    return StyleCard(
        id=style_id,
        name=name,
        description=description,
        body=body,
        path=path,
        updated_at=updated_at,
        kind=kind,
    )


def list_style_cards(workdir: Path) -> list[StyleCard]:
    root = styles_dir(workdir)
    if not root.is_dir():
        return []
    cards: list[StyleCard] = []
    for path in sorted(root.glob("*.md"), key=lambda p: p.name.lower()):
        if path.name.startswith("_"):
            continue
        card = _card_from_file(path)
        if card is not None:
            cards.append(card)
    return cards


def get_style_card(workdir: Path, style_id: str) -> StyleCard | None:
    sid = normalize_style_id(style_id)
    path = styles_dir(workdir) / f"{sid}.md"
    return _card_from_file(path)


def format_style_catalog(workdir: Path) -> str:
    cards = list_style_cards(workdir)
    if not cards:
        return "(no style cards yet — use save_style after analyzing sample text/code)"
    lines = []
    for c in cards:
        lines.append(f"- `{c.id}` [{c.kind}]: {c.description}")
    return "\n".join(lines)


def format_style_block(card: StyleCard) -> str:
    return (
        f"### Style card `{card.id}` — {card.name} (kind={card.kind})\n"
        f"{card.description}\n\n"
        f"{card.body.strip()}"
    )


def format_active_styles_preamble(workdir: Path, style_ids: list[str]) -> str:
    """Build a prompt prefix from selected style ids (skip missing)."""
    blocks: list[str] = []
    for raw in style_ids:
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            card = get_style_card(workdir, raw)
        except ValueError:
            continue
        if card is None:
            continue
        blocks.append(format_style_block(card))
    if not blocks:
        return ""
    return (
        "## Active style cards (follow when writing prose OR generating/editing code)\n\n"
        + "\n\n---\n\n".join(blocks)
        + "\n\n## User task\n"
    )


def merge_style_body(existing_body: str, additions: str, *, note: str = "") -> str:
    """Append a dated learning chunk; keep under size cap by truncating oldest middle if needed."""
    old = (existing_body or "").strip()
    add = (additions or "").strip()
    if not add:
        return old
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header = f"### Learned update ({ts})"
    if note.strip():
        header += f" — {note.strip()}"
    chunk = f"{header}\n{add}"
    if not old:
        merged = chunk
    else:
        merged = f"{old}\n\n{chunk}"
    if len(merged) <= _MAX_BODY_CHARS:
        return merged
    # Keep head (core rules) + newest chunk
    keep_head = max(800, _MAX_BODY_CHARS // 2)
    head = old[:keep_head].rstrip()
    merged = (
        f"{head}\n\n…[older refinements truncated to fit card size]…\n\n{chunk}"
    )
    if len(merged) > _MAX_BODY_CHARS:
        merged = merged[:_MAX_BODY_CHARS] + "\n\n…[truncated]"
    return merged


def save_style_card(
    workdir: Path,
    *,
    style_id: str,
    name: str,
    description: str,
    body: str,
    kind: str = "writing",
    overwrite: bool = True,
) -> StyleCard:
    sid = normalize_style_id(style_id)
    name_clean = (name or sid).strip() or sid
    desc = (description or "").strip()
    body_clean = (body or "").strip()
    kind_n = normalize_style_kind(kind)
    if not body_clean:
        raise ValueError("style body is empty")
    if len(body_clean) > _MAX_BODY_CHARS:
        raise ValueError(f"style body too long (max {_MAX_BODY_CHARS} chars)")

    root = styles_dir(workdir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{sid}.md"
    if path.exists() and not overwrite:
        raise FileExistsError(f"style `{sid}` already exists (pass overwrite=true)")

    text = _render_card_markdown(
        style_id=sid,
        name=name_clean,
        description=desc or f"Style card '{name_clean}'.",
        body=body_clean,
        kind=kind_n,
    )
    path.write_text(text, encoding="utf-8", newline="\n")
    card = _card_from_file(path)
    if card is None:
        raise OSError(f"failed to re-read style card at {path}")
    return card


def refine_style_card(
    workdir: Path,
    *,
    style_id: str,
    additions: str,
    note: str = "",
    kind: str | None = None,
    description: str | None = None,
) -> StyleCard:
    """Merge new observations into an existing card (incremental learning)."""
    existing = get_style_card(workdir, style_id)
    if existing is None:
        raise FileNotFoundError(f"style `{style_id}` not found — create with save_style first")
    add = (additions or "").strip()
    if not add:
        raise ValueError("additions is empty")
    merged = merge_style_body(existing.body, add, note=note)
    return save_style_card(
        workdir,
        style_id=existing.id,
        name=existing.name,
        description=(description if description is not None else existing.description),
        body=merged,
        kind=kind if kind is not None else existing.kind,
        overwrite=True,
    )


def preview_style_markdown(
    *,
    style_id: str,
    name: str,
    description: str,
    body: str,
    kind: str = "writing",
) -> str:
    sid = normalize_style_id(style_id)
    return _render_card_markdown(
        style_id=sid,
        name=(name or sid).strip() or sid,
        description=description,
        body=body,
        kind=kind,
    )


def delete_style_card(workdir: Path, style_id: str) -> bool:
    sid = normalize_style_id(style_id)
    path = styles_dir(workdir) / f"{sid}.md"
    if not path.is_file():
        return False
    path.unlink()
    return True
