"""Mid-run user steer: queue corrective instructions without stopping the agent."""

from __future__ import annotations

import threading


STEER_MARKER = "[user_steer]"


class SteerInbox:
    """Thread-safe queue of mid-run user messages (Web POST /api/steer)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: list[str] = []

    def push(self, text: str) -> bool:
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        with self._lock:
            self._pending.append(cleaned)
        return True

    def drain(self) -> list[str]:
        with self._lock:
            out = list(self._pending)
            self._pending.clear()
            return out

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)


def format_steer_message(text: str) -> str:
    return (
        f"{STEER_MARKER} 用户在运行中补充/纠正指令"
        f"（请立即按此调整方向；可更新 todo、停止无关操作）：\n{text.strip()}"
    )
