# Day3 演示任务（录视频用）

## 推荐命令（在仓库根目录、已激活环境）

```powershell
python -m src.main -w demos --approval auto --max-steps 20 "阅读 greeter_test.py，修复 greeter.py 使测试全部通过。请先用 todo_write 列出计划，再逐步执行，最后运行 python greeter_test.py 验证。"
```

## 预期过程（视频里应能看到）

1. `todo_write` 写出计划（读测试 → 读实现 → 修复 → 跑测试）
2. `read_file` 查看测试与源码
3. `edit_file` 把错误问候改成 `Hello, {name}!`
4. `run_shell` 运行 `python greeter_test.py`，输出 `ok`
5. 更新 todo 为 completed，给出最终说明

## 备用演示（更短）

```powershell
python -m src.main -w demos --approval auto "修复 buggy_calc.py，使 add(2,3)==5，并运行验证。先用 todo_write 规划。"
```
