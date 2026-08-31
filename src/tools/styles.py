"""Style-card tools: list / load / save / refine / delete (progressive, like skills)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.agent.styles import (
    delete_style_card,
    format_style_block,
    format_style_catalog,
    get_style_card,
    list_style_cards,
    merge_style_body,
    preview_style_markdown,
    refine_style_card,
    save_style_card,
)
from src.tools.base import FunctionTool, ToolRegistry
from src.tools.user_ask import UserAskFn

_YES_RE = re.compile(
    r"^\s*(y|yes|是|好|确认|同意|ok|okay|save|保存)\b",
    re.I,
)


def register_style_tools(
    registry: ToolRegistry,
    *,
    workdir: Path,
    ask_user_fn: UserAskFn | None = None,
) -> None:
    wd = Path(workdir).resolve()

    def list_styles(_args: dict[str, Any]) -> str:
        cards = list_style_cards(wd)
        if not cards:
            return (
                "No style cards yet. Analyze sample text/code, then save_style "
                "(prefer confirm=true so the user previews before disk write)."
            )
        lines = [f"Style cards ({len(cards)}) under .agent/styles/:"]
        for c in cards:
            lines.append(f"- `{c.id}` [{c.kind}] — {c.name}: {c.description}")
        lines.append("Use load_style(id); refine_style(id) to learn from new samples.")
        return "\n".join(lines)

    def load_style(args: dict[str, Any]) -> str:
        style_id = str(args.get("id") or args.get("name") or "").strip()
        if not style_id:
            return "Error: id is required"
        try:
            card = get_style_card(wd, style_id)
        except ValueError as exc:
            return f"Error: {exc}"
        if card is None:
            catalog = format_style_catalog(wd)
            return f"Error: style `{style_id}` not found.\nInstalled:\n{catalog}"
        return format_style_block(card)

    def save_style(args: dict[str, Any]) -> str:
        style_id = str(args.get("id") or args.get("name") or "").strip()
        name = str(args.get("title") or args.get("display_name") or style_id).strip()
        description = str(args.get("description") or "").strip()
        body = str(args.get("body") or "").strip()
        kind = str(args.get("kind") or "writing").strip()
        overwrite = bool(args.get("overwrite", True))
        preview_only = bool(args.get("preview_only", False))
        confirm = bool(args.get("confirm", True))

        if not style_id:
            return "Error: id is required (short slug, e.g. product-casual or py-compact)"
        if not body:
            return "Error: body is required (short actionable style rules)"

        try:
            preview = preview_style_markdown(
                style_id=style_id,
                name=name or style_id,
                description=description,
                body=body,
                kind=kind,
            )
        except ValueError as exc:
            return f"Error: {exc}"

        if preview_only:
            return (
                "Preview only (not saved). Show the user this card, then call "
                "save_style again with preview_only=false after they agree.\n\n"
                f"{preview}"
            )

        if confirm and ask_user_fn is not None:
            question = (
                f"确认保存风格卡片「{name or style_id}」(id=`{style_id}`, kind={kind}) "
                f"到 .agent/styles/ 吗？回复「是」保存，「否」取消。\n\n"
                f"预览：\n```markdown\n{preview}\n```"
            )
            answer = ask_user_fn(question)
            if isinstance(answer, str) and answer.startswith("Error:"):
                return answer
            if not _YES_RE.search(str(answer or "")):
                return (
                    f"Save cancelled by user (answer={answer!r}). "
                    "You may revise the card and call save_style again."
                )

        try:
            card = save_style_card(
                wd,
                style_id=style_id,
                name=name or style_id,
                description=description,
                body=body,
                kind=kind,
                overwrite=overwrite,
            )
        except FileExistsError as exc:
            return f"Error: {exc}"
        except ValueError as exc:
            return f"Error: {exc}"
        except OSError as exc:
            return f"Error: could not write style card: {exc}"

        note = ""
        if confirm and ask_user_fn is None:
            note = " (no interactive confirm available — saved directly)"
        return (
            f"Saved style card `{card.id}` [{card.kind}] → "
            f".agent/styles/{card.id}.md{note}\n"
            f"{format_style_block(card)}"
        )

    def refine_style(args: dict[str, Any]) -> str:
        style_id = str(args.get("id") or args.get("name") or "").strip()
        additions = str(args.get("additions") or args.get("new_rules") or "").strip()
        note = str(args.get("note") or "").strip()
        kind_raw = args.get("kind")
        kind = str(kind_raw).strip() if kind_raw not in (None, "") else None
        confirm = bool(args.get("confirm", True))
        preview_only = bool(args.get("preview_only", False))

        if not style_id:
            return "Error: id is required"
        if not additions:
            return (
                "Error: additions is required — short NEW rules learned from the "
                "latest sample (do not paste the whole file)."
            )

        try:
            existing = get_style_card(wd, style_id)
        except ValueError as exc:
            return f"Error: {exc}"
        if existing is None:
            return (
                f"Error: style `{style_id}` not found. Create it with save_style first."
            )

        merged_body = merge_style_body(existing.body, additions, note=note)
        use_kind = kind if kind else existing.kind
        try:
            preview = preview_style_markdown(
                style_id=existing.id,
                name=existing.name,
                description=existing.description,
                body=merged_body,
                kind=use_kind,
            )
        except ValueError as exc:
            return f"Error: {exc}"

        if preview_only:
            return (
                "Preview only (not saved). After user agrees, call refine_style "
                "again with preview_only=false.\n\n"
                f"{preview}"
            )

        if confirm and ask_user_fn is not None:
            question = (
                f"确认用新样例更新风格卡「{existing.name}」(id=`{existing.id}`)？"
                f"回复「是」合并保存，「否」取消。\n\n"
                f"新增要点：\n{additions[:1200]}\n\n"
                f"合并后预览：\n```markdown\n{preview}\n```"
            )
            answer = ask_user_fn(question)
            if isinstance(answer, str) and answer.startswith("Error:"):
                return answer
            if not _YES_RE.search(str(answer or "")):
                return f"Refine cancelled by user (answer={answer!r})."

        try:
            card = refine_style_card(
                wd,
                style_id=style_id,
                additions=additions,
                note=note,
                kind=kind,
            )
        except FileNotFoundError as exc:
            return f"Error: {exc}"
        except ValueError as exc:
            return f"Error: {exc}"
        except OSError as exc:
            return f"Error: could not refine style card: {exc}"

        return (
            f"Refined style card `{card.id}` [{card.kind}] "
            f"(.agent/styles/{card.id}.md)\n{format_style_block(card)}"
        )

    def delete_style(args: dict[str, Any]) -> str:
        style_id = str(args.get("id") or args.get("name") or "").strip()
        if not style_id:
            return "Error: id is required"
        confirm = bool(args.get("confirm", True))
        try:
            card = get_style_card(wd, style_id)
        except ValueError as exc:
            return f"Error: {exc}"
        if card is None:
            return f"Error: style `{style_id}` not found"

        if confirm and ask_user_fn is not None:
            answer = ask_user_fn(
                f"确认删除风格卡片「{card.name}」(id=`{card.id}`)？回复「是」删除。",
            )
            if isinstance(answer, str) and answer.startswith("Error:"):
                return answer
            if not _YES_RE.search(str(answer or "")):
                return f"Delete cancelled by user (answer={answer!r})."
        try:
            ok = delete_style_card(wd, style_id)
        except ValueError as exc:
            return f"Error: {exc}"
        if not ok:
            return f"Error: style `{style_id}` not found"
        return f"Deleted style card `{style_id}`."

    catalog = format_style_catalog(wd)
    registry.register(
        FunctionTool(
            name="list_styles",
            description=(
                "List saved style cards (writing/code) under .agent/styles/. "
                "Use before load_style / refine_style."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            handler=list_styles,
            risk_level="low",
            is_readonly=True,
        )
    )
    registry.register(
        FunctionTool(
            name="load_style",
            description=(
                "Load full rules for a style card by id (writing or code style). "
                f"Installed: {catalog}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Style id slug, e.g. 'py-compact'.",
                    }
                },
                "required": ["id"],
            },
            handler=load_style,
            risk_level="low",
            is_readonly=True,
        )
    )
    registry.register(
        FunctionTool(
            name="save_style",
            description=(
                "Create/overwrite a short style card from sample text OR code. "
                "kind=writing|code|mixed. Body = actionable rules "
                "(naming, layout, APIs, tone…) — NOT the full source. "
                "Default confirm=true for user preview. For updating an existing "
                "card with NEW samples, prefer refine_style."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Short slug id, e.g. py-compact.",
                    },
                    "title": {"type": "string", "description": "Human display name."},
                    "description": {
                        "type": "string",
                        "description": "One-line summary of when to use this style.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Markdown rules for imitating the style.",
                    },
                    "kind": {
                        "type": "string",
                        "description": "writing | code | mixed (default writing).",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Overwrite if id exists (default true).",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "Ask user to confirm preview before save.",
                    },
                    "preview_only": {
                        "type": "boolean",
                        "description": "If true, return preview only.",
                    },
                },
                "required": ["id", "body"],
            },
            handler=save_style,
            risk_level="medium",
            is_readonly=False,
        )
    )
    registry.register(
        FunctionTool(
            name="refine_style",
            description=(
                "Incrementally teach an EXISTING style card from new sample code/text. "
                "Pass only NEW observations in additions (naming, patterns, don'ts). "
                "Merges into the card with a dated section; confirm=true by default. "
                "Use after load_style + reading the new sample."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Existing style id."},
                    "additions": {
                        "type": "string",
                        "description": "Short NEW rules learned from the latest sample.",
                    },
                    "note": {
                        "type": "string",
                        "description": "Optional label, e.g. from greeter.py.",
                    },
                    "kind": {
                        "type": "string",
                        "description": "Optionally set/override kind to code|writing|mixed.",
                    },
                    "confirm": {"type": "boolean"},
                    "preview_only": {"type": "boolean"},
                },
                "required": ["id", "additions"],
            },
            handler=refine_style,
            risk_level="medium",
            is_readonly=False,
        )
    )
    registry.register(
        FunctionTool(
            name="delete_style",
            description=(
                "Delete a style card by id. Default confirm=true asks the user first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Style id to delete."},
                    "confirm": {
                        "type": "boolean",
                        "description": "Ask user before delete (default true).",
                    },
                },
                "required": ["id"],
            },
            handler=delete_style,
            risk_level="medium",
            is_readonly=False,
            destructive=True,
        )
    )
