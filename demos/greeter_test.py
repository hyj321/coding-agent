"""Tests for demos/greeter.py — run: python -m pytest greeter_test.py -q
Or without pytest: python greeter_test.py
"""

from greeter import greet


def test_greet_basic() -> None:
    assert greet("Agent") == "Hello, Agent!"


def test_greet_empty() -> None:
    assert greet("") == "Hello, !"


if __name__ == "__main__":
    test_greet_basic()
    test_greet_empty()
    print("ok")
