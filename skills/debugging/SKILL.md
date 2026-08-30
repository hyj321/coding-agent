---
name: debugging
description: Use when fixing bugs, failing tests, tracebacks, or unexpected runtime behavior. Do NOT use for greenfield features, pure refactors, adding tests on a green suite (use testing), or code review without a failure signal.
---

# Debugging

Follow this playbook. Prefer tools over guessing.

**Same turn:** If not preloaded, `load_skill("debugging")` + `todo_write` (3–5 phases) + first `grep`/`glob` (batch). Do not plan alone.

**Plan-then-act (Dec-B):** Multi-step locate→fix→verify **must** open with `todo_write` (phase-level, not one todo per tool). Trivial one-shot (single obvious edit, no investigation) may skip todo. Never claim done without `run_tests` evidence.

## Search-first (mandatory locate path)
1. **Capture the failure** — Read the error / failing assertion / traceback. Note expected vs actual strings.
2. **Locate with grep/glob** — `grep` the assertion text / symbol / function name; or `glob` `*test*`. Do **not** start with whole-tree `list_dir` or blind full-file `read_file`.
3. **Slice-read** — `read_file` with `offset`/`limit` around the hit. Long files auto-return a head window only — continue with offset, do not demand the whole file.
4. **Understand** — Minimal related code + the test. Root cause in one sentence.
5. **Plan** — `todo_write` with 3–5 **phase** items (locate → fix → verify). Keep one `in_progress`.
6. **Fix minimally** — Prefer `edit_file`. Invalid Python syntax is rejected and not written — fix the snippet and retry.
7. **Verify** — Prefer `run_tests` with `target` set to the test file (e.g. `greeter_test.py`). Fall back to `run_shell` only if needed.
8. **Loop** — If still failing, return to step 2 with the new error. Stop only when tests pass.
9. **Inspect changes (optional)** — `git_status` / `git_diff` after edits (read-only).

## Anti-patterns
- `list_dir` of the whole tree then reading every file "just in case"
- Full-file `read_file` on long sources when grep already gave a line number
- Using `run_shell` for ordinary test runs when `run_tests` is available
- Claiming success without running tests
- Large speculative rewrites
