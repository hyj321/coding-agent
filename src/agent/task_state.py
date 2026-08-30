"""Lightweight Task State for progress visibility (P0 MVP).

Harness-owned snapshot: goal, relevant files, last error, test status.
Injected into Current State; persisted via working_memory / transcript memory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_FAILED_COUNT = re.compile(r"(\d+)\s+failed", re.IGNORECASE)
_PASSED_COUNT = re.compile(r"(\d+)\s+passed", re.IGNORECASE)
_EXIT_CODE = re.compile(r"exit_code:\s*(-?\d+)", re.IGNORECASE)
_FAILED_LINE = re.compile(r"(?m)^(FAILED|ERROR)\b")


@dataclass
class TestStatus:
    last_command: str = ""
    passed: bool | None = None
    summary: str = ""
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_command": self.last_command,
            "passed": self.passed,
            "summary": self.summary,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TestStatus | None:
        if not isinstance(data, dict):
            return None
        return cls(
            last_command=str(data.get("last_command") or ""),
            passed=data.get("passed") if isinstance(data.get("passed"), bool) else None,
            summary=str(data.get("summary") or ""),
            fingerprint=str(data.get("fingerprint") or ""),
        )


@dataclass
class TaskState:
    goal: str = ""
    relevant_files: list[str] = field(default_factory=list)
    last_error: str = ""
    test_status: TestStatus | None = None
    stop_condition: str = "tests_all_pass"
    # Mirrored from RetryPolicy for persistence / UI (updated by ContextManager)
    failed: list[dict[str, Any]] = field(default_factory=list)
    retry_stage: int = 0
    final_nudge_sent: bool = False
    stop_nudge_reasons: list[str] = field(default_factory=list)
    files_mutated: bool = False
    evidence_nudge_count: int = 0
    tool_phase: str = "full"

    def note_file(self, path: str | None) -> None:
        if not path:
            return
        p = path.replace("\\", "/")
        if p in self.relevant_files:
            self.relevant_files.remove(p)
        self.relevant_files.insert(0, p)
        self.relevant_files = self.relevant_files[:8]

    def note_error(self, text: str) -> None:
        line = " ".join(text.strip().split())
        if not line:
            return
        if len(line) > 220:
            line = line[:220] + "…"
        self.last_error = line

    def update_from_tool(
        self,
        *,
        tool_name: str,
        args: dict[str, Any] | str | None,
        result: str,
        paths: list[str] | None = None,
    ) -> None:
        for p in paths or []:
            self.note_file(p)
        if isinstance(args, dict):
            path = args.get("path")
            if isinstance(path, str):
                self.note_file(path)

        ok = not str(result).startswith("Error")
        failedish = (not ok) or bool(
            re.search(r"(?m)^(FAILED|ERROR)\b|exit_code:\s*[1-9]", result)
        )
        if failedish:
            first = result.splitlines()[0] if result else ""
            self.note_error(first or result[:200])

        if ok and tool_name in {"write_file", "edit_file"}:
            self.files_mutated = True

        if tool_name == "run_shell" and isinstance(args, dict):
            cmd = str(args.get("command") or "")
            parsed = parse_test_status(cmd, result)
            if parsed is not None:
                self.test_status = parsed

    def render_block(self) -> str:
        lines = ["### Task State"]
        if self.goal:
            goal = self.goal if len(self.goal) <= 240 else self.goal[:237] + "…"
            lines.append(f"Goal: {goal}")
        if self.relevant_files:
            lines.append("Relevant files: " + ", ".join(self.relevant_files[:8]))
        if self.last_error:
            lines.append(f"Last error: {self.last_error}")
        if self.test_status is not None:
            ts = self.test_status
            if ts.passed is True:
                status = "PASSED"
            elif ts.passed is False:
                status = "FAILED"
            else:
                status = "unknown"
            lines.append(f"Test status: {status} — {ts.summary or '(no summary)'}")
            if ts.last_command:
                cmd = ts.last_command
                if len(cmd) > 100:
                    cmd = cmd[:97] + "…"
                lines.append(f"Last test cmd: {cmd}")
        lines.append(f"Stop condition: {self.stop_condition}")
        if self.retry_stage:
            lines.append(f"Retry stage (last): {self.retry_stage}")
        if self.failed:
            lines.append("Failed strategies (do not repeat):")
            for item in self.failed[:6]:
                key = item.get("key") if isinstance(item, dict) else item
                cnt = item.get("count") if isinstance(item, dict) else "?"
                stage = item.get("stage") if isinstance(item, dict) else "?"
                lines.append(f"- [{stage}] {key} (count={cnt})")
        if self.final_nudge_sent:
            reasons = ", ".join(self.stop_nudge_reasons) or "goal"
            lines.append(f"Stop nudge sent ({reasons}) — prefer FINAL, no more edits.")
        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "relevant_files": list(self.relevant_files),
            "last_error": self.last_error,
            "test_status": self.test_status.to_dict() if self.test_status else None,
            "stop_condition": self.stop_condition,
            "failed": list(self.failed)[:20],
            "retry_stage": self.retry_stage,
            "final_nudge_sent": self.final_nudge_sent,
            "stop_nudge_reasons": list(self.stop_nudge_reasons),
            "files_mutated": self.files_mutated,
            "evidence_nudge_count": self.evidence_nudge_count,
            "tool_phase": self.tool_phase,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TaskState:
        if not isinstance(data, dict):
            return cls()
        files = data.get("relevant_files")
        failed_raw = data.get("failed")
        failed: list[dict[str, Any]] = []
        if isinstance(failed_raw, list):
            failed = [x for x in failed_raw if isinstance(x, dict)][:20]
        reasons = data.get("stop_nudge_reasons")
        return cls(
            goal=str(data.get("goal") or ""),
            relevant_files=[str(x) for x in files][:8] if isinstance(files, list) else [],
            last_error=str(data.get("last_error") or ""),
            test_status=TestStatus.from_dict(
                data.get("test_status") if isinstance(data.get("test_status"), dict) else None
            ),
            stop_condition=str(data.get("stop_condition") or "tests_all_pass"),
            failed=failed,
            retry_stage=int(data.get("retry_stage") or 0),
            final_nudge_sent=bool(data.get("final_nudge_sent")),
            stop_nudge_reasons=[str(x) for x in reasons] if isinstance(reasons, list) else [],
            files_mutated=bool(data.get("files_mutated")),
            evidence_nudge_count=int(data.get("evidence_nudge_count") or 0),
            tool_phase=str(data.get("tool_phase") or "full"),
        )

    @classmethod
    def from_goal(cls, goal: str) -> TaskState:
        text = (goal or "").strip()
        return cls(goal=text[:500] if text else "")


def parse_test_status(command: str, output: str) -> TestStatus | None:
    """Best-effort parse of pytest / unittest style shell output.

    Returns None when the command does not look like a test run.
    """
    cmd_l = (command or "").lower()
    looks_like_test = any(
        token in cmd_l
        for token in ("pytest", "py.test", "unittest", "nosetests", "greeter_test")
    )
    if not looks_like_test and "test" not in cmd_l:
        return None

    text = output or ""
    exit_m = _EXIT_CODE.search(text)
    exit_code: int | None = int(exit_m.group(1)) if exit_m else None

    failed_m = _FAILED_COUNT.search(text)
    passed_m = _PASSED_COUNT.search(text)
    failed = int(failed_m.group(1)) if failed_m else None
    passed = int(passed_m.group(1)) if passed_m else None
    has_failed_line = bool(_FAILED_LINE.search(text))

    if failed is None and passed is None and exit_code is None and not has_failed_line:
        if not looks_like_test:
            return None

    all_passed: bool | None
    if failed is not None or passed is not None:
        all_passed = (failed or 0) == 0
        if exit_code is not None and exit_code != 0:
            all_passed = False
        if has_failed_line and (failed or 0) > 0:
            all_passed = False
    elif exit_code is not None:
        all_passed = exit_code == 0 and not has_failed_line
    elif has_failed_line:
        all_passed = False
    else:
        all_passed = None

    parts: list[str] = []
    if failed is not None:
        parts.append(f"{failed} failed")
    if passed is not None:
        parts.append(f"{passed} passed")
    if exit_code is not None:
        parts.append(f"exit={exit_code}")
    summary = ", ".join(parts) if parts else ("failed line present" if has_failed_line else "parsed")

    fp_bits = [
        "test",
        f"pass={all_passed}",
        f"f={failed}",
        f"p={passed}",
        f"e={exit_code}",
    ]
    return TestStatus(
        last_command=command.strip()[:200],
        passed=all_passed,
        summary=summary,
        fingerprint=":".join(str(b) for b in fp_bits),
    )
