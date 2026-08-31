---
name: style-transfer
description: Use when the user wants to learn, refine, or imitate a writing OR coding style (风格卡片 / style card / 代码风格). Do NOT use for debugging failing tests or pure behavior-preserving refactors.
---

# Style transfer (style cards)

Prefer style tools over dumping samples into MEMORY.

**Same turn:** `load_skill("style-transfer")` + `list_styles` (optional) + read sample file if given.

## Learn — writing
1. Read pasted text or `read_file`.
2. Extract short rules: tone, structure, vocabulary, don'ts, 1–2 sample lines.
3. `save_style` with `kind="writing"`, `confirm=true`.

## Learn — code style
1. Read sample module(s) with `read_file` (offset/limit if long). Look at:
   naming, imports, typing, error handling, logging, docstring/comment habits,
   test layout, file/package shape, forbidden patterns.
2. Card body = **rules + tiny snippets**, not the whole file.
3. `save_style` with `kind="code"` (or `mixed`), id like `py-compact`, `confirm=true`.

## Incremental teach (same card, new samples)
1. `load_style(id)` then read the **new** sample.
2. `refine_style(id, additions=<only NEW observations>, note=<filename>)` with confirm.
3. Do not re-paste the entire old card into additions. If the card gets noisy, rewrite once with `save_style` (compact full body).

## Imitate (write code/prose in that style)
1. If the task preamble already has "Active style cards", follow them.
2. Else `load_style(id)`.
3. `edit_file` / `write_file` matching the card. Prefer matching existing project tree.

## Manage
- Update fully: `save_style` same id + overwrite.
- Teach more: `refine_style`.
- Remove: `delete_style` with confirm.

## Anti-patterns
- Saving whole source files as the card body
- Creating a new id every time instead of `refine_style`
- Ignoring active style cards when the user attached/checked them
- Mixing unrelated bugfix into a style-only task
