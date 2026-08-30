---
name: refactoring
description: Use when restructuring code without changing behavior (rename, extract, tidy). Do NOT use when behavior is broken or tests fail (use debugging), or when the goal is new features / new tests only (implement normally or use testing).
---

# Refactoring

Follow this playbook. Prefer tools over guessing.

**Same turn:** If not preloaded, `load_skill("refactoring")` + `todo_write` (3–5 phases) + first reads. Do not plan alone.

1. **Baseline** — Locate related tests; run them first (`run_shell`). If already failing, switch to **debugging**.
2. **Scope** — One sentence: what structure changes, what must stay identical.
3. **Plan** — `todo_write`: baseline → small edit → re-test → repeat. Keep one `in_progress`.
4. **Change in small steps** — Prefer `edit_file`. No drive-by features or API breaks.
5. **Verify each step** — Re-run the same tests after each meaningful edit.
6. **Stop** — When structure goal is met and baseline tests still pass.

## Anti-patterns
- Refactoring on a red suite
- Mixing new behavior into a "refactor" task
- Large speculative rewrites without intermediate test runs
