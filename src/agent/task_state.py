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

# Test-path heuristics for V1/V2 evidence (fake-green / source mutation)
_TEST_FILE_RE = re.compile(
    r"(?:^|/)(?:conftest\.py|test_[^/]+\.py|[^/]+_test\.py)$",
    re.IGNORECASE,
)
_TEST_DIR_RE = re.compile(r"(?:^|/)(?:tests|test|__tests__)(?:/|$)", re.IGNORECASE)


def normalize_rel_path(path: str | None) -> str:
    if not path:
        return ""
    return path.replace("\\", "/").lstrip("./")


def is_test_path(path: str | None) -> bool:
    """True for common unit-test paths (*_test.py, test_*.py, tests/, conftest)."""
    p = normalize_rel_path(path)
    if not p:
        return False
    if _TEST_FILE_RE.search(p):
        return True
    if _TEST_DIR_RE.search(p) and p.endswith((".py", ".pyi")):
        return True
    return False


@dataclass
class TestStatus:
    last_command: str = ""
    passed: bool | None = None  # exit-level green (raw); gate uses semantic_tests_passed
    summary: str = ""
    fingerprint: str = ""
    exit_code: int | None = None
    # E2: paths/labels extracted from the test command (may be empty for legacy fixtures)
    targets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_command": self.last_command,
            "passed": self.passed,
            "summary": self.summary,
            "fingerprint": self.fingerprint,
            "exit_code": self.exit_code,
            "targets": list(self.targets)[:12],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TestStatus | None:
        if not isinstance(data, dict):
            return None
        raw_targets = data.get("targets")
        targets = (
            [normalize_rel_path(str(x)) for x in raw_targets if str(x).strip()][:12]
            if isinstance(raw_targets, list)
            else []
        )
        exit_raw = data.get("exit_code")
        exit_code = int(exit_raw) if isinstance(exit_raw, int) else None
        return cls(
            last_command=str(data.get("last_command") or ""),
            passed=data.get("passed") if isinstance(data.get("passed"), bool) else None,
            summary=str(data.get("summary") or ""),
            fingerprint=str(data.get("fingerprint") or ""),
            exit_code=exit_code,
            targets=targets,
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
    # Ver-A: successful write/edit paths (relative), newest first, capped
    mutated_paths: list[str] = field(default_factory=list)
    evidence_nudge_count: int = 0
    tool_phase: str = "full"

    def note_file(self, path: str | None) -> None:
        if not path:
            return
        p = normalize_rel_path(path)
        if not p:
            return
        if p in self.relevant_files:
            self.relevant_files.remove(p)
        self.relevant_files.insert(0, p)
        self.relevant_files = self.relevant_files[:8]

    def note_mutation(self, path: str | None) -> None:
        """Record a successful write/edit for Evidence Mustlist / fake-green."""
        p = normalize_rel_path(path)
        if not p:
            return
        self.files_mutated = True
        if p in self.mutated_paths:
            self.mutated_paths.remove(p)
        self.mutated_paths.insert(0, p)
        self.mutated_paths = self.mutated_paths[:24]
        self.note_file(p)

    def source_mutated_paths(self) -> list[str]:
        return [p for p in self.mutated_paths if not is_test_path(p)]

    def test_mutated_paths(self) -> list[str]:
        return [p for p in self.mutated_paths if is_test_path(p)]

    def only_tests_mutated(self) -> bool:
        """True when there was a write/edit and every mutated path looks like a test."""
        if not self.mutated_paths:
            return False
        return not self.source_mutated_paths()

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
            path_arg = None
            if isinstance(args, dict) and isinstance(args.get("path"), str):
                path_arg = args["path"]
            elif paths:
                path_arg = paths[0]
            if path_arg:
                self.note_mutation(path_arg)
            else:
                self.files_mutated = True

        if tool_name == "run_shell" and isinstance(args, dict):
            cmd = str(args.get("command") or "")
            parsed = parse_test_status(cmd, result)
            if parsed is not None:
                self.test_status = parsed
        elif tool_name == "run_tests":
            target = "."
            if isinstance(args, dict):
                raw = args.get("target")
                if isinstance(raw, str) and raw.strip():
                    target = raw.strip()
            # Unified parser (structured header + exit); always prefer run_tests label
            parsed = parse_test_status(f"run_tests {target}", result)
            if parsed is not None:
                if not parsed.targets:
                    parsed.targets = [normalize_rel_path(target)]
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
            cover = test_run_covers_task(self)
            if ts.passed is True and not cover:
                status = "PASSED(unrelated)"
            lines.append(f"Test status: {status} — {ts.summary or '(no summary)'}")
            if ts.targets:
                lines.append("Test targets: " + ", ".join(ts.targets[:5]))
            if ts.last_command:
                cmd = ts.last_command
                if len(cmd) > 100:
                    cmd = cmd[:97] + "…"
                lines.append(f"Last test cmd: {cmd}")
        if self.mutated_paths:
            src = self.source_mutated_paths()[:4]
            tst = self.test_mutated_paths()[:4]
            if src:
                lines.append("Mutated source: " + ", ".join(src))
            if tst:
                lines.append("Mutated tests: " + ", ".join(tst))
            if self.only_tests_mutated():
                lines.append("Evidence warning: only test files mutated (possible fake-green)")
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
            "mutated_paths": list(self.mutated_paths)[:24],
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
        mutated = data.get("mutated_paths")
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
            mutated_paths=(
                [normalize_rel_path(str(x)) for x in mutated if str(x).strip()][:24]
                if isinstance(mutated, list)
                else []
            ),
            evidence_nudge_count=int(data.get("evidence_nudge_count") or 0),
            tool_phase=str(data.get("tool_phase") or "full"),
        )

    @classmethod
    def from_goal(cls, goal: str) -> TaskState:
        text = (goal or "").strip()
        return cls(goal=text[:500] if text else "")


# Strong test-command signals (E2: bare substring "test" is NOT enough)
_STRONG_TEST_CMD_RE = re.compile(
    r"(?:^|[\s/\\=])(?:pytest|py\.test|unittest|nosetests|run_tests)\b|"
    r"(?:^|[\s\"'=])(?:[\w./\\-]+_test\.py|test_[\w./\\-]+\.py)\b",
    re.IGNORECASE,
)
_RUN_TESTS_PASSED_RE = re.compile(r"(?m)^passed:\s*(true|false)\s*$", re.IGNORECASE)
_PATHISH_RE = re.compile(
    r"(?:^|[\s\"'=])((?:[\w.-]+/)*[\w.-]+\.py|\.)(?=[\s\"']|$)",
    re.IGNORECASE,
)
_GOAL_TOKEN_RE = re.compile(r"[A-Za-z_][\w-]{1,40}")
_STOPWORDS = frozenset(
    {
        "test",
        "tests",
        "fix",
        "fail",
        "error",
        "bug",
        "file",
        "files",
        "run",
        "pytest",
        "python",
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "please",
        "make",
        "sure",
        "pass",
        "passed",
        "green",
        "task",
        "code",
        "src",
        "main",
        "true",
        "false",
        "auto",
        "unittest",
        "conftest",
        "repair",
        "update",
        "change",
        "modify",
        "implement",
        "write",
        "read",
        "edit",
        "demo",
        "demos",
    }
)


def looks_like_test_command(command: str) -> bool:
    """True only for pytest/unittest/run_tests or explicit *_test.py / test_*.py paths."""
    cmd = command or ""
    if _STRONG_TEST_CMD_RE.search(cmd):
        return True
    # Structured tool header from run_tests
    if re.search(r"(?m)^#\s*run_tests\b", cmd):
        return True
    return False


def extract_test_targets(command: str) -> list[str]:
    """Best-effort targets from a test command / run_tests label."""
    cmd = (command or "").strip()
    if not cmd:
        return []
    targets: list[str] = []

    m = re.match(r"(?i)^run_tests\s+(.+)$", cmd)
    if m:
        raw = m.group(1).strip().split()[0] if m.group(1).strip() else "."
        targets.append(normalize_rel_path(raw) or ".")
        return targets[:8]

    # Prefer explicit test module paths
    for hit in re.finditer(
        r"(?i)((?:[\w.-]+[/\\])*?(?:[\w.-]+_test\.py|test_[\w.-]+\.py))",
        cmd,
    ):
        targets.append(normalize_rel_path(hit.group(1).replace("\\", "/")))

    if targets:
        # de-dupe preserve order
        seen: set[str] = set()
        out: list[str] = []
        for t in targets:
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out[:8]

    # pytest/unittest with a path-ish arg
    if re.search(r"(?i)\b(?:pytest|py\.test|unittest)\b", cmd):
        for hit in _PATHISH_RE.finditer(cmd):
            tok = hit.group(1)
            if tok.lower() in {"pytest", "unittest", "python", "py"}:
                continue
            targets.append(normalize_rel_path(tok) or tok)
        if not targets:
            targets.append(".")
    return targets[:8]


def _path_stems(path: str) -> set[str]:
    p = normalize_rel_path(path)
    if not p or p in {".", "./"}:
        return set()
    name = p.rsplit("/", 1)[-1]
    stem = name[:-3] if name.lower().endswith(".py") else name
    stem = stem.lower()
    out: set[str] = set()
    if stem and stem not in _STOPWORDS:
        out.add(stem)
    if stem.endswith("_test") and len(stem) > 5:
        base = stem[: -len("_test")]
        if base and base not in _STOPWORDS:
            out.add(base)
    if stem.startswith("test_") and len(stem) > 5:
        base = stem[5:]
        if base and base not in _STOPWORDS:
            out.add(base)
    # parent dir name sometimes carries the module (tests/greeter/…)
    parts = [x for x in p.lower().split("/") if x and x not in {"tests", "test", "__tests__"}]
    for part in parts[:-1]:
        if part not in _STOPWORDS and len(part) >= 2:
            out.add(part)
    return out


def task_test_anchors(task_state: TaskState) -> set[str]:
    """Stems that identify what this task is about (files + goal tokens)."""
    anchors: set[str] = set()
    for p in list(task_state.source_mutated_paths()) + list(task_state.relevant_files):
        anchors |= _path_stems(p)
    for p in task_state.test_mutated_paths():
        anchors |= _path_stems(p)
    goal = task_state.goal or ""
    for tok in _GOAL_TOKEN_RE.findall(goal):
        low = tok.lower()
        if low in _STOPWORDS or len(low) < 3:
            continue
        anchors.add(low)
        # Chinese goals often mix latin module names; keep as-is
    return anchors


def _is_broad_target(target: str) -> bool:
    t = normalize_rel_path(target)
    return t in {"", ".", "./", "*"}


def test_run_covers_task(task_state: TaskState) -> bool:
    """E2: when we know task anchors, the last test run must touch them.

    Legacy fixtures with ``passed=True`` and empty command/targets still count
    as covering (no false regressions on Ver-A/B unit checks).
    Broad targets (``.`` / full suite) soft-cover everything.
    """
    ts = task_state.test_status
    if ts is None:
        return False

    anchors = task_test_anchors(task_state)
    targets = [normalize_rel_path(t) for t in (ts.targets or []) if str(t).strip()]
    if not targets:
        targets = extract_test_targets(ts.last_command)

    if not anchors:
        return True  # nothing to mismatch against

    if not targets:
        # Manual TestStatus(passed=True) without command → allow (unit fixtures)
        if not (ts.last_command or "").strip():
            return True
        # Opaque / non-extractable command while we have anchors → not semantic green
        return False

    if any(_is_broad_target(t) for t in targets):
        return True

    target_stems: set[str] = set()
    for t in targets:
        target_stems |= _path_stems(t)
    if not target_stems:
        return False
    return bool(anchors & target_stems)


def semantic_tests_passed(task_state: TaskState) -> bool:
    """Exit green AND (when applicable) the run covers task anchors."""
    ts = task_state.test_status
    if ts is None or ts.passed is not True:
        return False
    return test_run_covers_task(task_state)


def parse_test_status(command: str, output: str) -> TestStatus | None:
    """Unified parse of run_tests / pytest / unittest output.

    Returns None when the command does not look like a real test run (E2:
    substring ``test`` alone is insufficient — avoids treating ``echo test`` as green).
    """
    cmd = (command or "").strip()
    text = output or ""
    # Structured run_tests result always counts even if label is odd
    structured = bool(
        re.search(r"(?m)^#\s*run_tests\b", text) or _RUN_TESTS_PASSED_RE.search(text)
    )
    looks_like_test = looks_like_test_command(cmd) or structured
    if not looks_like_test:
        return None

    exit_m = _EXIT_CODE.search(text)
    exit_code: int | None = int(exit_m.group(1)) if exit_m else None

    passed_line = _RUN_TESTS_PASSED_RE.search(text)
    structured_passed: bool | None = None
    if passed_line:
        structured_passed = passed_line.group(1).lower() == "true"

    failed_m = _FAILED_COUNT.search(text)
    passed_m = _PASSED_COUNT.search(text)
    failed = int(failed_m.group(1)) if failed_m else None
    passed_n = int(passed_m.group(1)) if passed_m else None
    has_failed_line = bool(_FAILED_LINE.search(text))

    if (
        structured_passed is None
        and failed is None
        and passed_n is None
        and exit_code is None
        and not has_failed_line
    ):
        return None

    all_passed: bool | None
    if structured_passed is not None:
        all_passed = structured_passed
        if exit_code is not None and exit_code != 0:
            all_passed = False
    elif failed is not None or passed_n is not None:
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
    if passed_n is not None:
        parts.append(f"{passed_n} passed")
    if exit_code is not None:
        parts.append(f"exit={exit_code}")
    if structured_passed is not None and not parts:
        parts.append(f"passed={structured_passed}")
    summary = ", ".join(parts) if parts else ("failed line present" if has_failed_line else "parsed")

    targets = extract_test_targets(cmd)
    if not targets:
        # From "# run_tests pytest greeter_test.py"
        header = re.search(r"(?m)^#\s*run_tests\s+(.+)$", text)
        if header:
            targets = extract_test_targets("run_tests " + header.group(1).strip())

    fp_bits = [
        "test",
        f"pass={all_passed}",
        f"f={failed}",
        f"p={passed_n}",
        f"e={exit_code}",
        f"t={','.join(targets[:3])}",
    ]
    return TestStatus(
        last_command=cmd[:200],
        passed=all_passed,
        summary=summary,
        fingerprint=":".join(str(b) for b in fp_bits),
        exit_code=exit_code,
        targets=targets,
    )
