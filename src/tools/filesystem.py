"""Filesystem tools: read_file, write_file, list_dir, edit_file, glob, grep."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from src.agent.permissions import PermissionGate
from src.tools.fs_noise import is_noise_entry
from src.tools.base import FunctionTool, ToolRegistry

# Cap-A / SWE-agent style viewer: long files without offset → head window only
_AUTO_HEAD_MIN_LINES = 100
_AUTO_HEAD_LINES = 100
_DEFAULT_GREP_MAX_MATCHES = 50
_DEFAULT_GREP_MAX_FILES = 80
_BINARY_SAMPLE = 8192


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, total {len(text)} chars]"


def _parse_int(raw: Any, *, name: str) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"Error: {name} must be an integer") from None


def _is_probably_text(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:_BINARY_SAMPLE]
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    return True


def _python_syntax_error(path: Path, content: str) -> str | None:
    """Return an Error message if path is .py and content fails ast.parse; else None."""
    if path.suffix.lower() != ".py":
        return None
    try:
        ast.parse(content)
    except SyntaxError as exc:
        loc = f"line {exc.lineno}" if exc.lineno else "unknown line"
        msg = exc.msg or "invalid syntax"
        rel = path.name
        return (
            f"Error: syntax rejected for {rel} ({loc}): {msg}. "
            "File was NOT modified. Fix the snippet and retry."
        )
    return None



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
            offset = _parse_int(args.get("offset"), name="offset")
            limit = _parse_int(args.get("limit"), name="limit")
        except ValueError as exc:
            return str(exc)
        if offset is not None and offset < 1:
            return "Error: offset is 1-based; must be >= 1"
        if limit is not None and limit < 1:
            return "Error: limit must be >= 1"
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: cannot decode as UTF-8: {path}"

        lines = content.splitlines(keepends=True)
        total = len(lines)

        if offset is None and limit is None:
            if total >= _AUTO_HEAD_MIN_LINES:
                rel = path.relative_to(gate.workdir).as_posix()
                head_n = min(_AUTO_HEAD_LINES, total)
                body = "".join(lines[:head_n])
                next_off = head_n + 1
                header = (
                    f"# {rel} — lines 1-{head_n} of {total} "
                    f"(auto-head; full read omitted)\n"
                    f"# Continue: read_file path={rel!r} offset={next_off} "
                    f"limit={_AUTO_HEAD_LINES} — or grep for a symbol first.\n"
                )
                return _truncate(header + body, max_output_chars)
            return _truncate(content, max_output_chars)

        start = (offset or 1) - 1  # 0-based
        if start >= total and total > 0:
            return (
                f"Error: offset {offset or 1} past end of file "
                f"({total} lines). Use offset 1..{total}."
            )
        if limit is None:
            chunk = lines[start:]
            end_line = total
        else:
            chunk = lines[start : start + limit]
            end_line = start + len(chunk)
        body = "".join(chunk)
        rel = path.relative_to(gate.workdir).as_posix()
        header = f"# {rel} — lines {start + 1}-{end_line} of {total}\n"
        return _truncate(header + body, max_output_chars)

    def write_file(args: dict[str, Any]) -> str:
        path = gate.resolve_path(args["path"])
        content = args.get("content")
        if content is None:
            return "Error: missing required argument 'content'"
        text = str(content)
        syntax_err = _python_syntax_error(path, text)
        if syntax_err is not None:
            return syntax_err
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return f"Wrote {len(text)} chars to {path.relative_to(gate.workdir)}"

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
        syntax_err = _python_syntax_error(path, updated)
        if syntax_err is not None:
            return syntax_err
        path.write_text(updated, encoding="utf-8")
        rel = path.relative_to(gate.workdir).as_posix()
        n = count if replace_all else 1
        return f"Edited {rel}: replaced {n} occurrence(s)."

    def glob_files(args: dict[str, Any]) -> str:
        pattern = args.get("pattern")
        if not pattern or not isinstance(pattern, str):
            return "Error: missing required string argument 'pattern'"
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

    def grep_files(args: dict[str, Any]) -> str:
        pattern = args.get("pattern")
        if not pattern or not isinstance(pattern, str):
            return "Error: missing required string argument 'pattern'"
        try:
            flags = re.IGNORECASE if bool(args.get("case_insensitive", False)) else 0
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return f"Error: invalid regex: {exc}"

        path_arg = args.get("path") or "."
        try:
            search_root = gate.resolve_path(str(path_arg))
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"

        if not search_root.exists():
            return f"Error: path not found: {path_arg}"

        try:
            max_matches = _parse_int(args.get("max_matches"), name="max_matches")
        except ValueError as exc:
            return str(exc)
        if max_matches is None:
            max_matches = _DEFAULT_GREP_MAX_MATCHES
        if max_matches < 1:
            return "Error: max_matches must be >= 1"
        max_matches = min(max_matches, 200)

        files: list[Path] = []
        if search_root.is_file():
            files = [search_root]
        elif search_root.is_dir():
            for p in sorted(search_root.rglob("*")):
                if not p.is_file():
                    continue
                try:
                    rel = p.resolve().relative_to(gate.workdir.resolve())
                except (ValueError, OSError):
                    continue
                if any(is_noise_entry(part) for part in rel.parts):
                    continue
                files.append(p)
                if len(files) >= _DEFAULT_GREP_MAX_FILES * 4:
                    break
        else:
            return f"Error: not a file or directory: {path_arg}"

        hits: list[str] = []
        files_scanned = 0
        truncated = False
        for fpath in files:
            if search_root.is_dir() and files_scanned >= _DEFAULT_GREP_MAX_FILES:
                truncated = True
                break
            if not _is_probably_text(fpath):
                continue
            files_scanned += 1
            try:
                text = fpath.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rel = fpath.resolve().relative_to(gate.workdir.resolve()).as_posix()
            for i, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    preview = line if len(line) <= 200 else line[:200] + "…"
                    hits.append(f"{rel}:{i}:{preview}")
                    if len(hits) >= max_matches:
                        truncated = True
                        break
            if truncated and len(hits) >= max_matches:
                break

        if not hits:
            return f"(no matches for {pattern!r} under {path_arg})"
        footer = ""
        if truncated:
            footer = f"\n...[truncated at {len(hits)} matches / {files_scanned} files scanned]"
        header = f"# grep {pattern!r} in {path_arg} — {len(hits)} hit(s)\n"
        return _truncate(header + "\n".join(hits) + footer, max_output_chars)

    registry.register(
        FunctionTool(
            name="read_file",
            description=(
                "Read a UTF-8 text file under the working directory. "
                "Prefer grep first to locate, then offset/limit for a slice. "
                "Without offset/limit, files ≥100 lines return only an auto-head "
                "(first 100 lines) plus a continue hint — not the full file."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the working directory.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "1-based start line (inclusive). Omit to start at line 1.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of lines to return. Omit for the rest of the file.",
                    },
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
                "Creates parent directories if needed. "
                "For .py files, content must parse with ast; syntax errors leave the file unchanged."
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
            destructive=True,
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
                "old_string must match exactly (including whitespace). "
                "For .py files, the result must parse with ast; on syntax error the file is unchanged."
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
            destructive=True,
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
    registry.register(
        FunctionTool(
            name="grep",
            description=(
                "Search file contents with a regex under the working directory "
                "(path:line:text). Prefer grep over blind list_dir/read_file when "
                "locating symbols, errors, or failing assertions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Python/regex pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory relative to workdir (default '.').",
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "If true, ignore case (default false).",
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": "Cap on returned hits (default 50, max 200).",
                    },
                },
                "required": ["pattern"],
            },
            handler=grep_files,
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
