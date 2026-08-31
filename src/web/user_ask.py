"""Interactive user-question bridge for Web SSE + POST /api/ask_reply."""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

EmitFn = Callable[[dict[str, Any]], None]

DEFAULT_TIMEOUT_SEC = 600.0


class UserAskBridge:
    """Block until the user replies to ask_user (one pending at a time)."""

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
        self._answer: str = ""
        self._closed = False

    def ask(self, question: str, *, call_id: str | None = None) -> str:
        if self._closed:
            return "Error: ask_user unavailable (run finished)"
        if self._cancel_event is not None and self._cancel_event.is_set():
            return "Error: ask_user cancelled (run stopped)"

        q = (question or "").strip()
        if not q:
            return "Error: ask_user requires a non-empty question"

        request_id = uuid.uuid4().hex[:16]
        event = threading.Event()
        with self._lock:
            if self._closed:
                return "Error: ask_user unavailable (run finished)"
            self._event = event
            self._request_id = request_id
            self._answer = ""

        self._emit(
            {
                "type": "ask_user_request",
                "request_id": request_id,
                "question": q,
                "call_id": call_id,
                "timeout_sec": self._timeout_sec,
            }
        )

        remaining = float(self._timeout_sec)
        slice_sec = 0.25
        answered = False
        while remaining > 0:
            if self._closed or (
                self._cancel_event is not None and self._cancel_event.is_set()
            ):
                break
            wait = slice_sec if remaining > slice_sec else remaining
            if event.wait(timeout=wait):
                answered = True
                break
            remaining -= wait

        with self._lock:
            cancelled = self._closed or (
                self._cancel_event is not None and self._cancel_event.is_set()
            )
            timed_out = not answered and not cancelled
            text = self._answer.strip() if answered else ""
            if self._request_id == request_id:
                self._request_id = None
                self._event = None

        if cancelled:
            self._emit(
                {
                    "type": "ask_user_resolved",
                    "request_id": request_id,
                    "answered": False,
                    "reason": "cancelled",
                    "call_id": call_id,
                }
            )
            return "Error: user did not answer (run cancelled)"
        if timed_out:
            self._emit(
                {
                    "type": "ask_user_resolved",
                    "request_id": request_id,
                    "answered": False,
                    "reason": "timeout",
                    "call_id": call_id,
                }
            )
            return "Error: user did not answer (timeout)"
        if not text:
            self._emit(
                {
                    "type": "ask_user_resolved",
                    "request_id": request_id,
                    "answered": False,
                    "reason": "empty",
                    "call_id": call_id,
                }
            )
            return "Error: user replied with empty text"
        self._emit(
            {
                "type": "ask_user_resolved",
                "request_id": request_id,
                "answered": True,
                "reason": "user",
                "call_id": call_id,
            }
        )
        return f"User answer:\n{text}"

    def resolve(self, request_id: str, answer: str) -> dict[str, Any]:
        text = (answer or "").strip()
        with self._lock:
            if self._closed:
                return {"ok": False, "error": "run finished"}
            if self._request_id != request_id or self._event is None:
                return {"ok": False, "error": "no matching pending ask_user"}
            self._answer = text
            event = self._event
        event.set()
        return {"ok": True, "request_id": request_id, "answered": bool(text)}

    def pending_id(self) -> str | None:
        with self._lock:
            return self._request_id

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._event is not None:
                self._answer = ""
                self._event.set()
                self._event = None
                self._request_id = None
