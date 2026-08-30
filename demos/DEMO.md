# Day3 演示任务（录视频用）

## 推荐命令（在仓库根目录、已激活环境）

```powershell
python -m src.main -w demos --approval auto --max-steps 20 "阅读 greeter_test.py，修复 greeter.py 使测试全部通过。用 todo_write 写 3～5 条阶段计划（可与读文件同轮），阶段完成再更新 todo；最后运行 python greeter_test.py 验证。"
```

## 预期过程（视频里应能看到）

1. `todo_write` 写出**粗**计划（定位失败 → 最小修复 → 跑测），最好与 `read_file`/`glob` **同一步**
2. 读测试与源码（可同轮多文件）
3. `edit_file` 把错误问候改成 `Hello, {name}!`
4. `run_shell` 运行 `python greeter_test.py`，输出 `ok`
5. 阶段完成时更新 todo，给出最终说明

## 步数基线（P0 效率改造）

| 指标 | 说明 |
|------|------|
| 改造前参考 | 常见 5～15 步（细 todo / 单工具空步会偏高） |
| P0 目标 | 同任务 steps 下降；少「只 todo_write」的空步；撞 `max_steps` 变少 |
| 记录方式 | 看 CLI 最后 `steps=` 或 transcript；对比改造前后 |

## 备用演示（更短）

```powershell
python -m src.main -w demos --approval auto "修复 buggy_calc.py，使 add(2,3)==5，并运行验证。可用粗粒度 todo（3 条以内）。"
```
