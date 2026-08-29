# Code Agent

自研编程智能体（coding agent harness）：通过 DeepSeek（OpenAI 兼容）**tool calling**，在本地读写文件、执行命令，完成编程任务。

> 不使用 LangChain / AutoGen 等 agent 框架；循环、工具、上下文、权限、transcript 均自行实现。

仓库：https://github.com/hyj321/coding-agent

## 特色（Day3）

**Plan-then-Act（`todo_write`）**：非平凡任务先写检查清单，保持最多一项 `in_progress`，边做边更新，降低跑偏；过程在终端与 transcript 中可见，便于演示与答辩。

## 功能一览

- Agent 主循环 + `max_steps` / 用户中断
- 工具：`read_file` / `write_file` / `edit_file` / `list_dir` / `glob` / `run_shell` / `todo_write`
- 路径沙箱 + `--approval auto|ask|never`
- 上下文裁剪（`MAX_MESSAGES`）+ 工具输出截断
- **ACON 简化版 Context Manager**：分层上下文 + pytest 等观测压缩 + 超预算历史摘要折叠（`CONTEXT_TOKEN_BUDGET`）
- **长短期记忆（P0）**：`MEMORY.md` 跨 run 注入/追加；todo 完成时阶段压缩进 `history_summary`；Web 续写用 memory 快照 + 最近 K 条（非整包 prior）
- 运行 transcript → `transcripts/*.json`（含 `memory` 快照）
- 可选 Web UI（`python -m src.web`）

## 快速开始（CLI）

```powershell
conda create -n codeagent python=3.11 -y
conda activate codeagent
cd G:\codeagent
pip install -r requirements.txt
copy .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY

python -m scripts.smoke_v1
python -m src.main -w demos --approval auto "阅读 greeter_test.py，修复 greeter.py 使测试通过。先用 todo_write 规划，再执行，最后运行 python greeter_test.py。"
```

演示说明见 [`demos/DEMO.md`](demos/DEMO.md)。

## Web 界面（可选）

风格参考现代 AI 聊天产品（侧栏 + 问候 + 快捷卡片 + 底部输入），**背后仍调用同一套自研 agent**，不是套壳第三方 agent。

```powershell
conda activate codeagent
cd G:\codeagent
pip install -r requirements.txt
python -m src.web
```

浏览器打开：http://127.0.0.1:7860

- 点卡片或输入框提交任务
- **结构化步骤卡片**：完成的步骤自动折叠，点击可展开；**正在运行的步骤滚到屏幕中间**并高亮
- **富文本**：最终回复 / 思考 / 任务支持 Markdown（标题、列表、代码块等）
- **左侧栏固定**：不随中间区滚动；**Recent chats** 独立滚动；**一整轮对话 = 一条 session 历史**（多轮续写：喂模型用 memory + 最近 K，磁盘保留完整 messages）
- **Open folder**：顶部打开文件夹 → 更新 Workdir；右侧 **Files** 树可浏览，点击文件新窗口打开
- **Changed files**：`write_file` / `edit_file` 后底部列出改动，点击查看新旧对比
- **Plan 面板**：随 `todo_write` 实时勾选（右侧 Files / Plan 可切换、可折叠）
- 侧栏 **Reset demos**：把 `greeter.py` / `buggy_calc.py` 恢复为有意 bug
- 同时只允许一个任务运行（第二个返回 409）

CLI 仍然可用：`python -m src.main ...`

## 架构说明（面试可用）

```
用户任务
   ↓
CLI (src/main.py)
   ↓
Agent Loop (src/agent/loop.py)
   │  while step <= max_steps:
   │    trim_messages → chat(tools) → 若无 tool_calls 则结束
   │    对每个 tool_call: authorize → dispatch → append tool result
   ↓
┌──────────────┬─────────────────┬──────────────────┐
│ LLM Client   │ Tool Registry   │ Permissions      │
│ DeepSeek     │ schema+handler  │ 沙箱 + approval  │
│ OpenAI 兼容  │ 本地执行        │ hard-deny 危险命令│
└──────────────┴─────────────────┴──────────────────┘
         Context: Context Manager + MEMORY.md + 历史折叠
         Transcript: session / run JSON（含 memory 快照）
```

设计要点：

1. **模型只出意图，副作用在本地**——工具结果必须写回 messages，模型才能感知。
2. **新工具 = 注册表加一项**，主循环不必改。
3. **Todo 是一等工具**，不是另起一套框架；规划与执行仍在同一 loop 里。

## 环境变量 / CLI

| 变量或参数 | 说明 |
|------------|------|
| `DEEPSEEK_API_KEY` | 必填 |
| `BASE_URL` / `MODEL` | 默认 DeepSeek flash |
| `-w` | 工作目录沙箱 |
| `--approval` | `auto` / `ask` / `never` |
| `--max-steps` / `--max-messages` | 循环与上下文上限 |
| `--context-budget` / `CONTEXT_TOKEN_BUDGET` | Context Manager 近似 token 预算（默认 8000） |
| `--transcript-dir` | 默认 `transcripts`；`off` 关闭 |

进度与计划见 `计划书.md`。
