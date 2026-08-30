# Code Agent

自研编程智能体（coding agent harness）：通过 DeepSeek（OpenAI 兼容）**tool calling**，在本地读写文件、执行命令，完成编程任务。

> 不使用 LangChain / AutoGen 等 agent 框架；循环、工具、上下文、权限、transcript 均自行实现。

仓库：https://github.com/hyj321/coding-agent

## 特色（Day3）

**Plan-then-Act（`todo_write`）**：非平凡任务用 3～5 条**阶段**清单（可与读文件同轮），最多一项 `in_progress`，阶段边界再更新；平凡单改可跳过。过程在终端与 transcript 中可见。

**Skills（轻量）**：`skills/*/SKILL.md` 提供一类任务的可复用方法；system 只注入 name+description（L1），命中后用 `load_skill` 或**关键词预注入**加载正文（L2）。内置 `debugging` / `testing` / `refactoring`。

**上下文容量条**：输入框下方显示剩余容量%；将满时提示先总结。每轮结束自动写入 `MEMORY.md` / `.agent/last_turn_summary.md` 并在界面展示摘要。

## 功能一览

- Agent 主循环 + `max_steps` / 用户中断
- 工具：`read_file`（`offset`/`limit`；≥100 行 auto-head）/ `write_file` / `edit_file`（`.py` ast 护栏）/ `list_dir` / `glob` / `grep` / `run_shell` / **`run_tests`** / **`git_status`** / **`git_diff`** / `todo_write` / `load_skill` / `memory_search` / `rag_search`
- Skills：渐进披露（目录常驻 + `load_skill` / 关键词预注入），见 `skills/{debugging,testing,refactoring}/`
- 路径沙箱 + `--approval auto|ask|never`
- **三级风险元数据**（`risk_level` / `is_readonly`）：Low 自动放行；Medium/High 受 approval 约束；`.env` / SSH Key 等敏感路径 **始终 Deny**
- **网络/安装策略**（`NETWORK_POLICY=high|deny|allow`）：`pip`/`npm`/`curl` 等默认升 High；`deny` 时硬拒
- **子进程读密缓解**：拦 `cat`/`head`/`python -c`/`node -e` 等常见读 `.env` 绕过（**非** OS 沙箱；残余风险见下）
- **Least Privilege 可见集**（`TOOL_VISIBILITY=auto`）：按 todo 阶段收窄本步工具；**Completion Evidence Mustlist**：测绿 +（有写改时）源文件变更；**假绿防护**（`FAKE_GREEN_MODE=block|warn|off`）拒「只改测试变绿」
- **Web 护栏可见**：时间线气泡 EVIDENCE / FAKE_GREEN / DENY / APPROVAL / DEDUP；Medium/High 底部审批条
- **X3 soft-dedup**：同路径 `read_file` 且 mtime 未变 → 回摘要，不重复灌全文
- Web 交互审批（`approval=ask`）；敏感路径始终 Deny；无审批桥时 High 可 `deny_high`
- 上下文裁剪（`MAX_MESSAGES`）+ 工具输出截断
- **ACON 简化版 Context Manager**：分层上下文 + pytest 等观测压缩 + 超预算历史摘要折叠（`CONTEXT_TOKEN_BUDGET`）
- **长短期记忆（P0–P2）**：MEMORY.md、working_memory、续写瘦身、前缀/后缀布局、折叠重注、MicroCompact、失败对更新压缩 guideline、本地 TF–IDF `rag_search`、prompt-cache 钩子
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
python -m src.main -w demos --approval auto "阅读 greeter_test.py，修复 greeter.py 使测试通过。用粗粒度 todo（可与读文件同轮），最后运行 python greeter_test.py。"
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
- **护栏气泡**：完成证据不足 / 假绿 / 权限拒绝 / soft-dedup 会在时间线以彩色 INFO 标签出现；High 工具弹出底部审批条
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
│ DeepSeek     │ schema+handler  │ 沙箱 + 三级风险  │
│ OpenAI 兼容  │ risk_level 元数据│ Allow/Confirm/Deny│
└──────────────┴─────────────────┴──────────────────┘
         Context: Context Manager + MEMORY.md + 历史折叠
         Transcript: session / run JSON（含 memory 快照）
```

设计要点：

1. **模型只出意图，副作用在本地**——工具结果必须写回 messages，模型才能感知。
2. **新工具 = 注册表加一项**，主循环不必改。
3. **Todo 是一等工具**，不是另起一套框架；规划与执行仍在同一 loop 里。
4. **安全在工具边界执行**：`PermissionGate` 读 Registry 元数据 + 参数启发式（敏感路径 / 危险 shell / 网络安装 / 解释器读密），不依赖模型「保证遵守」。  
   **已知局限：** Windows 无 OS 级沙箱；编码混淆、间接脚本读密等无法靠字符串穷举——演示请用 `read_file .env` / `python -c open('.env')` 等常见路径。
5. **完成靠证据**：Harness 用测试 exit code / `run_tests` 决定能否 Terminate；模型自述「已修好」不够。

## Capability 边界（会做什么 / 不做什么）

**会做：** 工作区内读写改文件、内容/文件名搜索、跑测试与 shell、只读 git status/diff、Todo/Skill/轻量记忆。

**不做（刻意）：** 浏览器 / 外网检索、MCP、LSP 跳转、多子 Agent 并行、OS 级沙箱（仅路径沙箱）、自动 `git commit/push`。

更多质量标准与改造序见 [`Agent质量标准与改进路线.md`](./Agent质量标准与改进路线.md)。

## 统一 Eval 套件（Imp-A / I1，合并前门禁）

一条命令跑完 Cap + Dec + Cost + Ver + Sec 离线用例，输出统一表与 KPI：

```powershell
python -m scripts.run_suite_eval --offline
# 可选 live（需 API，Capability 任务）：
python -m scripts.run_suite_eval --live
```

表列：`dim | task | ok | done | steps | viol | patho | stopped`；汇总含完成率 / 违规率 / 病理率 / avg_steps。

## Capability Eval（Cap-C）

```powershell
# 离线（无 API）：指标打分 + 种 bug / run_tests 路径
python -m scripts.run_capability_eval --offline

# 在线（需 DEEPSEEK_API_KEY）：locate-string + fix-greeter，输出 steps 表
python -m scripts.run_capability_eval --live
# 只跑一个任务：
python -m scripts.run_capability_eval --live --task fix-greeter
```

Live 结果默认写入 `evals/results/live_*.json`，把表里的 `steps` 记进质量文档 §13 作基线。

## Cost Eval（Cost-C）

```powershell
python -m scripts.run_cost_eval --offline
python -m scripts.run_cost_eval --live
python -m scripts.run_cost_eval --live --low-budget 8000
```

Live 结果写入 `evals/results/cost_live_*.json`（`tokens_total_est` / 低压 `budget_exhausted` 基线见质量文档 §13）。

## 环境变量 / CLI

| 变量或参数 | 说明 |
|------------|------|
| `DEEPSEEK_API_KEY` | 必填 |
| `BASE_URL` / `MODEL` | 默认 DeepSeek flash |
| `-w` | 工作目录沙箱 |
| `--approval` | `auto` / `ask` / `never`（Low 始终放行；Medium/High 受此约束；敏感路径与 hard-deny 始终拒绝） |
| `TOOL_VISIBILITY` | `auto`（按阶段收窄工具）/ `off`（全量） |
| `COMPLETION_MODE` | `evidence`（默认，改代码需测试证据）/ `trust_model` |
| `EVIDENCE_NUDGE_MAX` | 证据催促次数上限（默认 2，耗尽后放行防卡死） |
| `FAKE_GREEN_MODE` | `block`（默认）/ `warn` / `off`：仅改测试文件却测绿时的处理 |
| `NETWORK_POLICY` | `high`（默认，pip/npm/curl 升 High）/ `deny`（硬拒）/ `allow`（不因此升级） |
| `DENY_HIGH` | `true` 时 High 在 auto 下也拒绝（Web 默认开启） |
| `--max-steps` / `--max-messages` | 循环与上下文上限 |
| `--context-budget` / `CONTEXT_TOKEN_BUDGET` | Context Manager 近似 token 预算（默认 **32000**） |
| `--max-task-tokens` / `MAX_TASK_TOKENS` | 任务累计 token 硬闸（默认 **0=关闭**；触顶 `budget_exhausted`） |
| `TASK_BUDGET_WARN_RATIO` | 剩余 ≤ 该比例时注入一次性 `[budget_warn]`（默认 **0.20**） |
| `--transcript-dir` | 默认 `transcripts`；`off` 关闭 |

进度与计划见 `计划书.md`。
