"""Filesystem tools: read_file, write_file, list_dir."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agent.permissions import PermissionGate
from src.tools.fs_noise import is_noise_entry
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
            if is_noise_entry(child.name):
                continue
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

    def edit_file(args: dict[str, Any]) -> str:
        path = gate.resolve_path(args["path"])
        old = args.get("old_string")
        new = args.get("new_string")
        if old is None or new is None:
            return "Error: edit_file requires 'old_string' and 'new_string'"
        old_s = str(old)
        new_s = str(new)
        if old_s == "":
            return "Error: old_string must not be empty"
        if not path.exists():
            return f"Error: file not found: {path}"
        if not path.is_file():
            return f"Error: not a file: {path}"
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: cannot decode as UTF-8: {path}"

        count = content.count(old_s)
        if count == 0:
            return (
                "Error: old_string not found in file. "
                "Read the file again and use an exact contiguous substring."
            )
        replace_all = bool(args.get("replace_all", False))
        if count > 1 and not replace_all:
            return (
                f"Error: old_string matched {count} times. "
                "Provide a more specific old_string, or set replace_all=true."
            )
        updated = content.replace(old_s, new_s) if replace_all else content.replace(old_s, new_s, 1)
        path.write_text(updated, encoding="utf-8")
        rel = path.relative_to(gate.workdir).as_posix()
        n = count if replace_all else 1
        return f"Edited {rel}: replaced {n} occurrence(s)."

    def glob_files(args: dict[str, Any]) -> str:
        pattern = args.get("pattern")
        if not pattern or not isinstance(pattern, str):
            return "Error: missing required string argument 'pattern'"
        # Prevent escaping via absolute patterns; resolve matches under workdir only.
        root = gate.workdir
        matches: list[str] = []
        for match in sorted(root.glob(pattern)):
            try:
                resolved = match.resolve()
                resolved.relative_to(root)
            except (ValueError, OSError):
                continue
            if resolved.is_file():
                rel = resolved.relative_to(root).as_posix()
                if any(is_noise_entry(part) for part in Path(rel).parts):
                    continue
                matches.append(rel)
        if not matches:
            return f"(no files matched pattern {pattern!r})"
        return _truncate("\n".join(matches), max_output_chars)

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
            risk_level="low",
            is_readonly=True,
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
            risk_level="medium",
            is_readonly=False,
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
            risk_level="low",
            is_readonly=True,
        )
    )
    registry.register(
        FunctionTool(
            name="edit_file",
            description=(
                "Replace an exact substring in an existing UTF-8 file. "
                "Prefer this over write_file for small, precise edits. "
                "old_string must match exactly (including whitespace)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the working directory.",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact text to find in the file.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Replacement text.",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "If true, replace every match; default false (single match required).",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
            handler=edit_file,
            risk_level="medium",
            is_readonly=False,
        )
    )
    registry.register(
        FunctionTool(
            name="glob",
            description=(
                "Find files under the working directory matching a glob pattern "
                "(e.g. '**/*.py', 'src/**/*.ts'). Returns relative paths."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern relative to the working directory.",
                    }
                },
                "required": ["pattern"],
            },
            handler=glob_files,
            risk_level="low",
            is_readonly=True,
        )
    )


def snapshot_workdir(workdir: Path, *, max_entries: int = 40) -> str:
    """Lightweight top-level listing for the system prompt (V1 helper)."""
    entries: list[str] = []
    try:
        children = sorted(workdir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return f"(unable to list workdir: {exc})"
    for child in children[: max_entries * 2]:
        if is_noise_entry(child.name):
            continue
        kind = "dir" if child.is_dir() else "file"
        entries.append(f"[{kind}] {child.name}")
        if len(entries) >= max_entries:
            break
    more = max(0, len([c for c in children if not is_noise_entry(c.name)]) - len(entries))
    if more > 0:
        entries.append(f"... and {more} more")
    return "\n".join(entries) if entries else "(empty)"


__all__ = ["register_filesystem_tools", "snapshot_workdir"]
