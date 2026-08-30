---
name: testing
description: Use when adding tests, strengthening coverage, or running a test suite that is not yet failing. Do NOT use when a test/assertion already fails with a clear bug (use debugging), or for pure refactors with green tests (use refactoring).
---

# Testing

Follow this playbook. Prefer tools over guessing.

**Same turn:** If not preloaded, `load_skill("testing")` + `todo_write` (3–5 phases) + first `glob`/`read_file`. Do not spend a turn on planning alone.

1. **Survey** — Find existing tests (`glob` `*test*`, `*_test.py`, `test_*.py`). Read the closest ones; match their style.
2. **Target** — Note what behavior to cover (happy path + one edge). One sentence.
3. **Plan** — `todo_write`: locate fixtures → write/adjust tests → run → fix reds. Keep one `in_progress`.
4. **Write minimally** — Prefer `edit_file` / small `write_file`. Do not invent a new test framework.
5. **Run** — `run_shell` the relevant tests. Note pass/fail.
6. **If red** — Treat as a bug: follow the **debugging** skill (or `load_skill("debugging")`) instead of stacking more tests.
7. **Stop** — When the suite (or targeted tests) is green and coverage goal is met.

## Anti-patterns
- Writing tests before reading existing ones
- Claiming "tests pass" without `run_shell`
- Mixing large feature work into a testing-only task
