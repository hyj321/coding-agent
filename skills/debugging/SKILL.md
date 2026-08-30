---
name: debugging
description: Use when fixing bugs, failing tests, tracebacks, or unexpected runtime behavior. Do NOT use for greenfield features, pure refactors, or code review without a failure signal.
---

# Debugging

Follow this playbook. Prefer tools over guessing.

1. **Capture the failure** — Read the error / failing assertion / traceback. Note expected vs actual.
2. **Locate** — Use `glob` (and targeted `read_file`) to find the failing test and the code under test. Do **not** blindly `list_dir` the whole tree.
3. **Understand** — Read the minimal related code + the test. Identify the root cause in one sentence.
4. **Plan** — Call `todo_write` with 3–5 **phase** items (locate → fix → verify). Keep one `in_progress`. Prefer the **same turn** as first `read_file`/`glob` (do not plan alone). Update todos only when a phase finishes.
5. **Fix minimally** — Prefer `edit_file` for a small change. Avoid rewriting whole files.
6. **Verify** — Run the relevant test via `run_shell` (e.g. `python greeter_test.py`).
7. **Loop** — If still failing, return to step 2 with the new error. Update todos. Stop only when tests pass.

## Anti-patterns
- Reading unrelated files "just in case"
- Claiming success without running tests
- Large speculative rewrites
