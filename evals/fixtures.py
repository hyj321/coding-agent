"""Shared demo fixtures for Capability eval (avoid importing Web stack)."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMOS_SRC = PROJECT_ROOT / "demos"

GREETER_BUGGY = '''"""Demo greeter with an intentional bug (for Day3 video / acceptance)."""


def greet(name: str) -> str:
    # BUG: should return "Hello, {name}!"
    return f"Hi {name}"
'''

GREETER_FIXED = '''"""Demo greeter with an intentional bug (for Day3 video / acceptance)."""


def greet(name: str) -> str:
    return f"Hello, {name}!"
'''
