"""Interactive approval bridge for Web SSE + POST /api/approve.

Worker thread calls ask() which emits approval_request over SSE and blocks
until the browser POSTs a decision (or timeout / cancel / Stop).
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

from src.agent.permissions import ApprovalPrompt

EmitFn = Callable[[dict[str, Any]], None]

DEFAULT_TIMEOUT_SEC = 300.0


class ApprovalBridge:
    """One pending approval at a time (Web already serializes runs)."""

    def __init__(
        self,
        emit: EmitFn,
        *,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._emit = emit
        self._timeout_sec = timeout_sec
        self._cancel_event = cancel_event
        self._lock = threading.Lock()
        self._event: threading.Event | None = None
        self._request_id: str | None = None
        self._result: bool = False
        self._closed = False

    def ask(self, prompt: ApprovalPrompt | str) -> bool:
        if self._closed:
            return False
        if self._cancel_event is not None and self._cancel_event.is_set():
            return False

        if isinstance(prompt, ApprovalPrompt):
            tool_name = prompt.tool_name
            risk_level = prompt.risk_level
            summary = prompt.summary
            call_id = prompt.call_id
            arguments = prompt.arguments
        else:
            tool_name = "unknown"
            risk_level = "high"
            summary = str(prompt)
            call_id = None
            arguments = {}

        request_id = uuid.uuid4().hex[:16]
        event = threading.Event()
        with self._lock:
            if self._closed:
                return False
            # Only one pending ask (tool loop is sequential)
            if self._event is not None:
                self._result = False
                self._event.set()
            self._event = event
            self._request_id = request_id
            self._result = False

        self._emit(
            {
                "type": "approval_request",
                "request_id": request_id,
                "tool_name": tool_name,
                "risk_level": risk_level,
                "summary": summary,
                "call_id": call_id,
                "arguments": _safe_args(arguments),
                "timeout_sec": self._timeout_sec,
            }
        )

        # Poll so Stop can unblock without waiting full timeout
        remaining = float(self._timeout_sec)
        slice_sec = 0.25
        ok = False
        while remaining > 0:
            if self._closed or (
                self._cancel_event is not None and self._cancel_event.is_set()
            ):
                break
            wait = slice_sec if remaining > slice_sec else remaining
            if event.wait(timeout=wait):
                ok = True
                break
            remaining -= wait

        with self._lock:
            cancelled = self._closed or (
                self._cancel_event is not None and self._cancel_event.is_set()
            )
            timed_out = not ok and not cancelled
            allowed = bool(self._result) if ok else False
            if self._request_id == request_id:
                self._request_id = None
                self._event = None
            if cancelled:
                self._emit(
                    {
                        "type": "approval_resolved",
                        "request_id": request_id,
                        "allowed": False,
                        "reason": "cancelled",
                        "tool_name": tool_name,
                        "call_id": call_id,
                    }
                )
                return False
            if timed_out:
                self._emit(
                    {
                        "type": "approval_resolved",
                        "request_id": request_id,
                        "allowed": False,
                        "reason": "timeout",
                        "tool_name": tool_name,
                        "call_id": call_id,
                    }
                )
            return allowed

    def resolve(self, request_id: str, allowed: bool) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                return {"ok": False, "error": "run finished"}
            if self._request_id != request_id or self._event is None:
                return {"ok": False, "error": "no matching pending approval"}
            self._result = bool(allowed)
            event = self._event
        event.set()
        self._emit(
            {
                "type": "approval_resolved",
                "request_id": request_id,
                "allowed": bool(allowed),
                "reason": "user",
            }
        )
        return {"ok": True, "allowed": bool(allowed), "request_id": request_id}

    def pending_id(self) -> str | None:
        with self._lock:
            return self._request_id

    def close(self) -> None:
        """Cancel any waiter when the run ends (deny)."""
        with self._lock:
            self._closed = True
            if self._event is not None:
                self._result = False
                self._event.set()
                self._event = None
                self._request_id = None


def _safe_args(arguments: dict[str, Any], limit: int = 400) -> dict[str, Any]:
    """Shallow copy with truncated string values for SSE payload."""
    out: dict[str, Any] = {}
    for key, value in list(arguments.items())[:12]:
        if isinstance(value, str):
            text = value.replace("\n", "\\n")
            out[key] = text if len(text) <= limit else text[:limit] + "..."
        elif isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
        else:
            text = str(value)
            out[key] = text if len(text) <= limit else text[:limit] + "..."
    return out
