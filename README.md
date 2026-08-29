# Code Agent (V1)

自研的最小可扩展编程智能体（coding agent harness）：通过 DeepSeek（OpenAI 兼容）的 **tool calling**，在本地读写文件、执行命令，完成你交给它的编程任务。

> 不使用 LangChain / AutoGen 等 agent 框架；工具执行、对话历史、循环终止均自行实现。

## 功能（V1）

- Agent 主循环：`call model → tool_calls → 本地执行 → append → 再 call`
- 工具：`read_file` / `write_file` / `list_dir` / `run_shell`
- 工作目录路径沙箱（文件操作不可逃逸 `workdir`）
- 工具错误以字符串返回给模型，便于自我纠正
- 终止：无工具调用的最终回复 / `max_steps` / Ctrl+C
- 过程日志：每步打印工具名、参数摘要、结果摘要

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. 配置密钥（不要提交 .env）
copy .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY

# 3. 运行
python -m src.main "在当前目录创建一个 hello.py，打印 Hello Agent，并用 python 运行它"
```

常用参数：

```bash
python -m src.main -w ./demos --max-steps 15 "列出目录并说明有哪些文件"
python -m src.main -m deepseek-v4-pro "修复 xxx"
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` 或 `API_KEY` | API 密钥（必填） |
| `BASE_URL` | 默认 `https://api.deepseek.com` |
| `MODEL` | 默认 `deepseek-v4-flash` |
| `WORKDIR` | 默认 `.` |
| `MAX_STEPS` | 默认 `20` |

## 架构（可扩展）

```
CLI → Agent Loop → LLM Client (DeepSeek)
                ↘ Tool Registry → filesystem / shell
                ↘ Context (system prompt)
                ↘ Permissions (path sandbox)
```

- **新工具**：在 `src/tools/` 实现并在 `build_default_registry` 注册即可，主循环不用改。
- **换模型**：改 `BASE_URL` / `MODEL`。
- **Day2+**：`edit_file`、上下文裁剪、approval、transcript、Todo 等按 `计划书.md` 推进。

## 项目结构

见 `计划书.md`。开发进度也记在该文件中。
