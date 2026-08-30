"""User-cancel helpers for cooperative agent stop."""

from __future__ import annotations

import threading
from typing import Callable


def is_cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def build_interrupt_message(*, changed_files: list[str] | None = None) -> str:
    """User-facing FINAL when the user hits Stop."""
    lines = [
        "已按你的要求停止。",
        "可以说「改成只修测试」或「撤销刚才的改动」继续。",
    ]
    files = [p for p in (changed_files or []) if p]
    # de-dupe, keep order
    seen: set[str] = set()
    ordered: list[str] = []
    for p in files:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    if ordered:
        lines.append("")
        lines.append("本轮已改动的文件：")
        for p in ordered[:20]:
            lines.append(f"- `{p}`")
        if len(ordered) > 20:
            lines.append(f"- …另有 {len(ordered) - 20} 个文件")
    return "\n".join(lines)


CancelCheck = Callable[[], bool]
