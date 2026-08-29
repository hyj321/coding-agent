"""Intentional bug for Day2 edit_file / self-repair demo."""


def add(a: int, b: int) -> int:
    # BUG: should return a + b
    return a - b


if __name__ == "__main__":
    assert add(2, 3) == 5, f"expected 5, got {add(2, 3)}"
    print("ok")
