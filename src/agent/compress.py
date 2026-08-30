"""Observation compression — ACON-style tool-result condensations (rule-based).

Inspired by ACON (Kang et al.): compress environment observations aggressively
while preserving failure signals agents need to recover.

Tiers (MicroCompact-style, P2):
  stub     — one-line placeholder for old / huge payloads
  summary  — failure card / head+tail extract
  full     — soft/hard clip of structured compression (default on ingest)

Guideline soft/hard limits can be tightened from failure pairs
(see acon_guideline.py) — no offline distilled compressor.
"""

from __future__ import annotations

import re
from typing import Any, Literal

CompressTier = Literal["stub", "summary", "full"]

# --- Default compression guideline (ACON would refine this from failures) ---
#
# KEEP: exit codes, FAILED/ERROR test names, exception types + short messages,
#       file paths touched, todo list text, short edit confirmations.
# COMPRESS: long pytest/unittest logs, lengthy successful stdout, huge reads.
# DROP: decorative separators, repeated stack frames beyond the top frames.


_FAILED_LINE = re.compile(
    r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-+\s+(.*))?$",
    re.MULTILINE,
)
_PYTEST_SUMMARY = re.compile(
    r"=+\s*(\d+\s+failed.*?)\s*=+",
    re.IGNORECASE | re.DOTALL,
)
_ASSERT_BLOCK = re.compile(
    r"(E\s+AssertionError:[^\n]*(?:\nE\s+[^\n]*)*)",
)
_TRACE_HEAD = re.compile(
    r"(Traceback \(most recent call last\):(?:\n.*?)?"
    r"(?:\n\w*(?:Error|Exception|Fail)[^\n]*))",
    re.DOTALL,
)
_EXIT_CODE = re.compile(r"exit_code:\s*(-?\d+)", re.IGNORECASE)
_PATH_IN_ARGS = re.compile(
    r'["\']?(?:path|file)["\']?\s*[:=]\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def estimate_tokens(text: str) -> int:
    """Cheap token proxy (~4 chars / token). Good enough for budget gates."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        tcs = m.get("tool_calls") or []
        for tc in tcs:
            fn = tc.get("function") or {}
            total += estimate_tokens(str(fn.get("name") or ""))
            total += estimate_tokens(str(fn.get("arguments") or ""))
        total += 4  # role / framing overhead
    return total


def stub_tool_result(tool_name: str, result: str, *, limit: int = 180) -> str:
    """MicroCompact tier-1: replace a large tool payload with a one-line stub."""
    flat = " ".join((result or "").split())
    ok = not (result or "").startswith("Error")
    mark = "ok" if ok else "ERR"
    if "failed" in flat.lower() or "Error" in (result or "")[:80]:
        mark = "ERR"
    preview = flat[: max(40, limit - 40)]
    if len(flat) > len(preview):
        preview += "…"
    return f"[stub:{tool_name}|{mark}|{len(result or 0)}c] {preview}"


def compress_tool_result(
    tool_name: str,
    result: str,
    *,
    soft_limit: int = 1200,
    hard_limit: int = 2400,
    tier: CompressTier = "full",
    stub_limit: int = 180,
) -> str:
    """Compress a tool observation before it enters the agent message list.

    soft_limit / hard_limit are character budgets (not tokens).
    """
    if not result:
        return result

    if tier == "stub":
        return stub_tool_result(tool_name, result, limit=stub_limit)

    if result.startswith("Error:"):
        return _clip(result, hard_limit if tier == "full" else min(hard_limit, soft_limit))

    name = (tool_name or "").lower()
    if name in {"run_shell", "run_tests"}:
        compressed = _compress_shell(result)
    elif name == "todo_write":
        return result  # already compact checklist
    elif name in {
        "read_file",
        "list_dir",
        "glob",
        "grep",
        "memory_search",
        "rag_search",
        "git_status",
        "git_diff",
    }:
        compressed = _compress_readish(result, soft_limit)
    elif name in {"write_file", "edit_file"}:
        return _clip(result, soft_limit)
    else:
        compressed = result

    if tier == "summary":
        extracted = _extract_failures(compressed) or compressed
        return _clip(extracted, min(soft_limit, 800))

    if len(compressed) <= soft_limit:
        return compressed
    # Second pass: structured extract if still long
    extracted = _extract_failures(compressed)
    if extracted and len(extracted) < len(compressed):
        compressed = extracted
    return _clip(compressed, hard_limit)


def microcompact_messages(
    messages: list[dict[str, Any]],
    *,
    keep_recent_tools: int = 4,
    stub_limit: int = 180,
    min_chars_to_stub: int = 400,
) -> tuple[list[dict[str, Any]], int]:
    """Stub older tool results in-place (copy); returns (messages, stub_count).

    Keeps the last `keep_recent_tools` tool messages intact; older bulky ones
    become one-line stubs — Claude-Code-style MicroCompact before full fold.
    """
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_indices) <= keep_recent_tools:
        return [dict(m) for m in messages], 0

    protect = set(tool_indices[-keep_recent_tools:])
    out: list[dict[str, Any]] = []
    stubs = 0
    for i, m in enumerate(messages):
        if i in protect or m.get("role") != "tool":
            out.append(dict(m))
            continue
        content = m.get("content")
        if not isinstance(content, str) or len(content) < min_chars_to_stub:
            out.append(dict(m))
            continue
        if content.startswith("[stub:"):
            out.append(dict(m))
            continue
        # Best-effort tool name from preceding assistant tool_calls
        name = _tool_name_for_index(messages, i) or "tool"
        out.append({**m, "content": stub_tool_result(name, content, limit=stub_limit)})
        stubs += 1
    return out, stubs


def _tool_name_for_index(messages: list[dict[str, Any]], tool_index: int) -> str | None:
    call_id = messages[tool_index].get("tool_call_id")
    for j in range(tool_index - 1, -1, -1):
        m = messages[j]
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if call_id and tc.get("id") == call_id:
                fn = tc.get("function") or {}
                return str(fn.get("name") or "tool")
        # fall back to first tool call name on that assistant turn
        tcs = m.get("tool_calls") or []
        if tcs:
            fn = tcs[0].get("function") or {}
            return str(fn.get("name") or "tool")
        break
    return None


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[compressed, total {len(text)} chars]"


def _compress_shell(result: str) -> str:
    failures = _extract_failures(result)
    exit_m = _EXIT_CODE.search(result)
    exit_line = f"exit_code: {exit_m.group(1)}" if exit_m else ""

    if failures:
        parts = [p for p in (exit_line, failures) if p]
        return "\n".join(parts)

    # Successful / uninformative long stdout → keep head+tail
    lines = result.splitlines()
    if len(lines) <= 40 and len(result) <= 1200:
        return result
    head = "\n".join(lines[:12])
    tail = "\n".join(lines[-8:])
    mid = f"\n...[{len(lines) - 20} lines omitted]...\n"
    body = head + mid + tail
    if exit_line and exit_line not in body:
        body = exit_line + "\n" + body
    return body


def _extract_failures(text: str) -> str:
    """Turn verbose test logs into a short failure card."""
    failed: list[tuple[str, str]] = []
    for m in _FAILED_LINE.finditer(text):
        kind, name, rest = m.group(1), m.group(2), (m.group(3) or "").strip()
        failed.append((f"{kind} {name}", rest))

    # pytest node ids sometimes only appear in summary section
    if not failed:
        for m in re.finditer(
            r"FAILED\s+([\w./:\\-]+(?:::[\w\[\].-]+)?)",
            text,
        ):
            failed.append((f"FAILED {m.group(1)}", ""))

    errors: list[str] = []
    for m in re.finditer(
        r"^(\w*(?:Error|Exception|Fail))\s*:\s*(.*)$",
        text,
        re.MULTILINE,
    ):
        errors.append(f"{m.group(1)}: {m.group(2).strip()}")

    # Deduplicate errors preserving order
    seen: set[str] = set()
    uniq_errors: list[str] = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            uniq_errors.append(e)

    summary_m = re.search(
        r"(\d+)\s+failed(?:,\s*(\d+)\s+passed)?",
        text,
        re.IGNORECASE,
    )

    if not failed and not uniq_errors and not summary_m:
        # Last resort: top of traceback
        tm = _TRACE_HEAD.search(text)
        if tm:
            return _clip(tm.group(1), 900)
        return ""

    lines: list[str] = []
    if summary_m:
        n_fail = summary_m.group(1)
        n_pass = summary_m.group(2)
        header = f"{n_fail} failed"
        if n_pass:
            header += f", {n_pass} passed"
        lines.append(header)
    elif failed:
        lines.append(f"{len(failed)} failed")

    for i, (title, rest) in enumerate(failed[:8], 1):
        lines.append(f"{i}. {title}")
        detail = rest
        if not detail and i - 1 < len(uniq_errors):
            detail = uniq_errors[i - 1]
        if detail:
            lines.append(f"   {detail}")

    if not failed and uniq_errors:
        for e in uniq_errors[:5]:
            lines.append(f"- {e}")

    return "\n".join(lines)


def _compress_readish(result: str, soft_limit: int) -> str:
    if len(result) <= soft_limit:
        return result
    lines = result.splitlines()
    if len(lines) <= 60:
        return _clip(result, soft_limit)
    head = "\n".join(lines[:25])
    tail = "\n".join(lines[-10:])
    return (
        head
        + f"\n...[{len(lines) - 35} lines omitted — re-read with a narrower path if needed]...\n"
        + tail
    )


def extract_paths_from_args(raw_args: str | dict[str, Any]) -> list[str]:
    paths: list[str] = []
    if isinstance(raw_args, dict):
        for key in ("path", "file"):
            if raw_args.get(key):
                paths.append(str(raw_args[key]).replace("\\", "/"))
        return paths
    for m in _PATH_IN_ARGS.finditer(str(raw_args)):
        paths.append(m.group(1).replace("\\", "/"))
    # bare JSON "path": "..."
    try:
        data = raw_args if isinstance(raw_args, dict) else json_loads_safe(raw_args)
        if isinstance(data, dict) and data.get("path"):
            p = str(data["path"]).replace("\\", "/")
            if p not in paths:
                paths.append(p)
    except Exception:  # noqa: BLE001
        pass
    return paths


def json_loads_safe(raw: str) -> Any:
    import json

    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def related_test_paths(path: str) -> list[str]:
    """Heuristic related tests for Relevant Context Retrieval."""
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    parent = p.rsplit("/", 1)[0] if "/" in p else ""
    candidates = [
        f"test_{stem}.py",
        f"{stem}_test.py",
        f"tests/test_{stem}.py",
        f"tests/{stem}_test.py",
    ]
    if parent:
        candidates.extend(
            [
                f"{parent}/test_{stem}.py",
                f"{parent}/{stem}_test.py",
            ]
        )
    # de-dup
    out: list[str] = []
    for c in candidates:
        if c not in out and c != p:
            out.append(c)
    return out
