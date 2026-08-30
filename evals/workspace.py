"""Workspace helpers for Capability eval (temp demos copy + plant bug)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from evals.fixtures import DEMOS_SRC, GREETER_BUGGY, GREETER_FIXED


def prepare_workdir(
    dest: Path,
    *,
    plant_greeter_bug: bool,
) -> Path:
    """Copy demos → dest and optionally plant the greeter bug. Returns dest."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    keep = {
        "greeter.py",
        "greeter_test.py",
        "buggy_calc.py",
        "DEMO.md",
        "README.md",
    }
    if not DEMOS_SRC.is_dir():
        raise FileNotFoundError(f"demos not found: {DEMOS_SRC}")
    for child in DEMOS_SRC.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_file() and (child.name in keep or child.suffix == ".py"):
            shutil.copy2(child, dest / child.name)
    greeter = dest / "greeter.py"
    if plant_greeter_bug:
        greeter.write_text(GREETER_BUGGY, encoding="utf-8")
    elif not greeter.exists():
        greeter.write_text(GREETER_FIXED, encoding="utf-8")
    # Always ensure test file exists
    test_py = dest / "greeter_test.py"
    if not test_py.exists():
        raise FileNotFoundError("greeter_test.py missing from demos copy")
    return dest


def tests_are_green(workdir: Path) -> bool:
    """Run greeter_test.py the same way as run_tests runner=python."""
    test_py = workdir / "greeter_test.py"
    if not test_py.exists():
        return False
    try:
        completed = subprocess.run(
            [sys.executable, str(test_py)],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0
