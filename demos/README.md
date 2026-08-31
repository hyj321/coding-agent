# Tiny demo workspace for the coding agent

Put sample projects here for videos and acceptance tests.

## Layout convention

| Kind | Where |
|------|--------|
| Day3 fixtures (`greeter*`, `buggy_calc*`) | workdir root (keep flat for video scripts) |
| Non-trivial apps (timer, fireworks, …) | `apps/<name>/` |
| One-line smokes / scratch | `smoke/` or `_scratch/` |
| Agent memory / scratch meta | `.agent/` |

Do **not** create a nested `demos/demos/` tree. Prefer modules + co-located `*_test.py` over one giant file when the task has multiple parts.
