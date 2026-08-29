"""Filesystem tools: read_file, write_file, list_dir."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agent.permissions import PermissionGate
from src.tools.base import FunctionTool, ToolRegistry


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, total {len(text)} chars]"


def register_filesystem_tools(
    registry: ToolRegistry,
    gate: PermissionGate,
    *,
    max_output_chars: int = 8000,
) -> None:
    def read_file(args: dict[str, Any]) -> str:
        path = gate.resolve_path(args["path"])
        if not path.exists():
            return f"Error: file not found: {path}"
        if not path.is_file():
            return f"Error: not a file: {path}"
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: cannot decode as UTF-8: {path}"
        return _truncate(content, max_output_chars)

    def write_file(args: dict[str, Any]) -> str:
        path = gate.resolve_path(args["path"])
        content = args.get("content")
        if content is None:
            return "Error: missing required argument 'content'"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content), encoding="utf-8")
        return f"Wrote {len(str(content))} chars to {path.relative_to(gate.workdir)}"

    def list_dir(args: dict[str, Any]) -> str:
        rel = args.get("path") or "."
        path = gate.resolve_path(rel)
        if not path.exists():
            return f"Error: directory not found: {path}"
        if not path.is_dir():
            return f"Error: not a directory: {path}"

        entries: list[str] = []
        for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                child.relative_to(gate.workdir)
            except ValueError:
                continue
            kind = "dir" if child.is_dir() else "file"
            rel_name = child.relative_to(gate.workdir).as_posix()
            entries.append(f"[{kind}] {rel_name}")
        if not entries:
            return "(empty directory)"
        return _truncate("\n".join(entries), max_output_chars)

    registry.register(
        FunctionTool(
            name="read_file",
            description=(
                "Read a UTF-8 text file under the working directory. "
                "Path is relative to workdir unless absolute and still inside workdir."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the working directory.",
                    }
                },
                "required": ["path"],
            },
            handler=read_file,
        )
    )
    registry.register(
        FunctionTool(
            name="write_file",
            description=(
                "Create or overwrite a UTF-8 text file under the working directory. "
                "Creates parent directories if needed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the working directory.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full file contents to write.",
                    },
                },
                "required": ["path", "content"],
            },
            handler=write_file,
        )
    )
    registry.register(
        FunctionTool(
            name="list_dir",
            description="List files and subdirectories under a path in the working directory.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to workdir. Defaults to '.'.",
                    }
                },
                "required": [],
            },
            handler=list_dir,
        )
    )


def snapshot_workdir(workdir: Path, *, max_entries: int = 40) -> str:
    """Lightweight top-level listing for the system prompt (V1 helper)."""
    entries: list[str] = []
    try:
        children = sorted(workdir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return f"(unable to list workdir: {exc})"
    for child in children[:max_entries]:
        kind = "dir" if child.is_dir() else "file"
        entries.append(f"[{kind}] {child.name}")
    more = len(children) - max_entries
    if more > 0:
        entries.append(f"... and {more} more")
    return "\n".join(entries) if entries else "(empty)"


__all__ = ["register_filesystem_tools", "snapshot_workdir"]
