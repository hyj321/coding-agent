"""Fixed Capability tasks for Cap-C / I1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CapabilityTask:
    id: str
    title: str
    prompt: str
    workdir_mode: Literal["demos_copy", "demos"]
    """demos_copy = temp copy of demos with planted bug; demos = use repo demos as-is."""
    plant_greeter_bug: bool
    max_steps: int
    require_grep: bool
    require_run_tests: bool
    """If True, success needs tests green after the run (fix-greeter)."""
    require_tests_green: bool
    success_path_hint: str


TASKS: dict[str, CapabilityTask] = {
    "locate-string": CapabilityTask(
        id="locate-string",
        title="Locate greet definition via search-first",
        prompt=(
            "在工作区里定位函数 greet 的定义：必须先用 grep 或 glob 搜索，"
            "再按需 read_file（可带 offset/limit）。不要整目录盲目 list_dir。"
            "找到后用简体中文说明它在哪个文件、返回什么格式的字符串。"
            "本任务不要修改任何文件。"
        ),
        workdir_mode="demos_copy",
        plant_greeter_bug=False,
        max_steps=12,
        require_grep=True,
        require_run_tests=False,
        require_tests_green=False,
        success_path_hint="greeter.py",
    ),
    "fix-greeter": CapabilityTask(
        id="fix-greeter",
        title="Fix greeter so tests pass",
        prompt=(
            "阅读 greeter_test.py，修复 greeter.py 使测试全部通过。"
            "用 todo_write 写 3～5 条阶段计划（可与搜索/读文件同轮）；"
            "定位时优先 grep；验证时优先 run_tests（target=greeter_test.py，"
            "runner=python），不要只用 run_shell。"
            "完成后用简体中文简要说明改了什么。"
        ),
        workdir_mode="demos_copy",
        plant_greeter_bug=True,
        max_steps=20,
        require_grep=False,  # soft preference; scored separately as search_first
        require_run_tests=True,
        require_tests_green=True,
        success_path_hint="greeter.py",
    ),
}


def get_task(task_id: str) -> CapabilityTask:
    if task_id not in TASKS:
        known = ", ".join(sorted(TASKS))
        raise KeyError(f"unknown task {task_id!r}; known: {known}")
    return TASKS[task_id]
