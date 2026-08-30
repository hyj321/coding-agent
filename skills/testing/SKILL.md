---
name: testing
description: Use when adding tests, strengthening coverage, or running a test suite that is not yet failing. Do NOT use when a test/assertion already fails with a clear bug (use debugging), or for pure refactors with green tests (use refactoring).
---

# Testing

Follow this playbook. Prefer tools over guessing.

**Same turn:** If not preloaded, `load_skill("testing")` + `todo_write` (3–5 phases) + first `glob`/`grep`. Do not spend a turn on planning alone.

**Plan-then-act:** Survey → plan phases with `todo_write` → write → `run_tests`. Skip todo only for a single trivial assertion tweak with an obvious target file.

## Search-first
1. **Survey** — Find tests with `glob` (`*test*`, `*_test.py`, `test_*.py`) or `grep` for `def test_`. Read the closest ones with `offset`/`limit` if long; match their style.
2. **Target** — Note what behavior to cover (happy path + one edge). One sentence.
3. **Plan** — `todo_write`: locate fixtures → write/adjust tests → run → fix reds. Keep one `in_progress`.
4. **Write minimally** — Prefer `edit_file` / small `write_file`. Syntax-invalid `.py` is rejected (file unchanged).
5. **Run** — Prefer `run_tests` (`target` = test file or `.`). Note `passed` / `exit_code`.
6. **If red** — Follow the **debugging** skill (`grep` the failure → slice-read → fix). Do not stack more tests blindly.
7. **Stop** — When the suite (or targeted tests) is green and coverage goal is met.

## Anti-patterns
- Writing tests before `glob`/`grep` for existing ones
- Blind full-tree `list_dir` to "discover" tests
- Claiming "tests pass" without `run_tests` / `run_shell`
- Mixing large feature work into a testing-only task
