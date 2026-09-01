# Code Agent

自研 **Coding Agent Harness**：通过 DeepSeek（OpenAI 兼容）**tool calling**，在本地读写文件、执行命令、跑测试，完成编程任务。

> **不使用** LangChain / AutoGen 等 Agent 框架；循环、工具、上下文、权限、transcript 均自行实现。  
> 设计原则：**模型只出意图，副作用在本地；安全与完成判定在 Harness 层执行，不依赖模型自觉。**

---

## 1. Git 仓库地址

- **GitHub：** https://github.com/hyj321/coding-agent  
- **Clone（HTTPS）：**

```bash
git clone https://github.com/hyj321/coding-agent.git
cd coding-agent
```

- **Clone（SSH）：**

```bash
git clone git@github.com:hyj321/coding-agent.git
cd coding-agent
```

---

## 2. 如何运行

### 2.1 环境准备

需要 Python 3.11+，以及可用的 DeepSeek API Key。

```powershell
conda create -n codeagent python=3.11 -y
conda activate codeagent
pip install -r requirements.txt
copy .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

（Linux / macOS 可用 `cp .env.example .env`。）

### 2.2 CLI 快速体验

```powershell
conda activate codeagent

# 冒烟（可选）
python -m scripts.smoke_v1

# 在 demos 沙箱里修 greeter
python -m src.main -w demos --approval auto "阅读 greeter_test.py，修复 greeter.py 使测试通过。用粗粒度 todo（可与读文件同轮），最后运行测试。"
```

更多演示说明见 [`demos/DEMO.md`](demos/DEMO.md)。

### 2.3 Web 界面（推荐演示）

```powershell
conda activate codeagent
python -m src.web
```

浏览器打开：http://127.0.0.1:7860

- 点快捷卡片或输入框提交任务（背后是同一套自研 Agent，不是套壳第三方）
- 时间线可见护栏气泡：EVIDENCE / FAKE_GREEN / DENY / APPROVAL / DEDUP
- 右侧 Plan / Files；底部可看 Changed files 对比；High 风险工具可弹出审批条
- 侧栏 **Reset demos** 可把演示文件恢复为有意 bug

### 2.4 离线评测（可选）

```powershell
python -m scripts.run_suite_eval --offline
```

统一跑 Cap / Dec / Cost / Ver / Sec 等离线用例。质量标准见 [`Agent质量标准与改进路线.md`](./Agent质量标准与改进路线.md)。

### 2.5 常用参数（摘要）

| 变量 / 参数 | 说明 |
|-------------|------|
| `DEEPSEEK_API_KEY` | 必填 |
| `-w` | 工作目录沙箱 |
| `--approval` | `auto` / `ask` / `never` |
| `TOOL_VISIBILITY` | `auto`（按阶段收窄工具）/ `off` |
| `COMPLETION_MODE` | `evidence`（默认）/ `trust_model` |
| `FAKE_GREEN_MODE` | `block`（默认）/ `warn` / `off` |
| `CONTEXT_TOKEN_BUDGET` | 上下文近似 token 预算（默认 32000） |

---

## 3. 特色功能说明与创新点

### 3.1 项目定位（一句话）

同一模型，换不同 Harness，可靠性可以差很多。本仓库把 **完成验证、工具可见性、长任务上下文** 做成可解释、可配置、可离线回归的机制，而不是只堆 Prompt。

### 3.2 三大创新点

#### 创新点一：完成靠证据，不靠自述 —— Completion Evidence Gate

模型说「做完了」不算数。无 `tool_calls` 想结束时，Harness 检查 Mustlist：

- 测试语义通过（不只是随便一个命令 exit 0）
- 跑的是与任务相关的用例（E2）
- 若改过代码，至少改过业务源文件；拦截「只改测试骗绿」（Fake Green）

不满足则注入 nudge 继续 loop（默认最多催 2 次防卡死）。Web 时间线出现 EVIDENCE / FAKE_GREEN 气泡。

> 金句：**Agent 的 Done 应该是 Harness 的状态，不是模型的语气。**  
> 细讲：[docs/面试创新点/01-完成靠证据-CompletionEvidenceGate.md](docs/面试创新点/01-完成靠证据-CompletionEvidenceGate.md)

#### 创新点二：按阶段收窄工具面 —— Least-Privilege Tool Visibility

每步调 LLM 前，从 in-progress todo 或 goal 推断阶段，动态收窄本步 **tool schema**：

| 阶段 | 行为 |
|------|------|
| explore | 只读（搜/读/git diff…），不给写与 shell |
| edit | 全工具 |
| verify | 可测 + `edit_file`，隐藏 `write_file` |
| full | 信号弱时不收窄，避免捆死 Agent |

与 `PermissionGate` 分工：可见性管「看不看得见」，权限管「允不允许执行」。Todo 由模型按需 `todo_write`，平凡任务可跳过。

> 金句：**Least Privilege 也可以作用在 Agent 的工具菜单上。**  
> 细讲：[docs/面试创新点/02-按阶段收窄工具面-ToolVisibility.md](docs/面试创新点/02-按阶段收窄工具面-ToolVisibility.md)

#### 创新点三：长任务不靠截断，靠分层记忆 —— ACON Context Manager

超窗不靠从前往后傻删消息。采用分层上下文 + 观测压缩：

- 五层：System / Task / Current State / Recent / Historical  
- 稳定前缀 + 可变后缀（利 prompt cache）  
- 同文件未改 **soft-dedup**；将满先 MicroCompact，再 Fold，并回注 MEMORY / focus / 未完成 todo  
- 跨 run：`MEMORY.md`、`working_memory.json`、Episode JSONL、本地 TF–IDF RAG 预取  

> 金句：**长任务的瓶颈往往是上下文怎么折叠而不丢状态。**  
> 细讲：[docs/面试创新点/03-分层上下文-ContextManager.md](docs/面试创新点/03-分层上下文-ContextManager.md)

三者关系（同一状态机上的三个阀门）：

```text
Todo / 阶段  →  Visibility 收窄本步能力
             →  Context Manager 管本步提示词与记忆
想结束时     →  Completion Gate 验证据
```

### 3.3 其他特色功能

- **Plan-then-Act**：`todo_write` 粗粒度阶段清单；可与读文件同轮批量  
- **Skills（渐进披露）**：L1 目录常驻，L2 `load_skill` / 关键词预注入；内置 debugging / testing / refactoring  
- **安全多层**：路径沙箱、三级 `risk_level`、敏感路径硬 Deny、`NETWORK_POLICY`、子进程读密启发式（**非** OS 级沙箱，局限已披露）  
- **工具面**：读写改、grep/glob、`run_tests`、只读 git、memory/rag 搜索等  
- **Web**：步骤卡片、上下文容量条、Changed files 对比、Plan 面板、护栏气泡与审批条  
- **Eval**：`run_suite_eval` 统一离线表（完成率 / 违规 / 病理 / 步数等）

### 3.4 能力边界（刻意不做）

**会做：** 工作区内读写改、搜索、跑测试与 shell、Todo/Skill/轻量记忆。  

**不做：** 浏览器 / 外网检索、MCP、LSP、多子 Agent 并行、OS 级沙箱、自动 `git commit/push`。

### 3.5 架构示意

```text
用户任务
   ↓
CLI (src/main.py)  或  Web (src/web)
   ↓
Agent Loop (src/agent/loop.py)
   while step ≤ max_steps:
     可见性收窄 tools → prepare_messages → chat
     若无 tool_calls → Completion Gate → 结束或 nudge
     否则 authorize → dispatch → 压缩观测 → 写回 messages
   ↓
LLM Client · Tool Registry · PermissionGate · Context Manager · Transcript
```

---

## 文档导航

| 文档 | 内容 |
|------|------|
| [demos/DEMO.md](demos/DEMO.md) | 演示任务 |
| [docs/理解Agent工作流/](docs/理解Agent工作流/) | 工作流拆解 |
| [docs/面试创新点/](docs/面试创新点/) | 三大创新点细讲 |
| [Agent质量标准与改进路线.md](./Agent质量标准与改进路线.md) | 质量维与改造序 |
| [计划书.md](./计划书.md) | 进度与计划 |
