# Agent 质量标准与改进路线

> **用途：** 交付后 / 有余力时，按本文档的维度与优先级做调研与改造。  
> **判定标准：** 好的 Agent = 能力完整 + 决策合理 + 上下文有效 + 执行可靠 + 结果可验证 + 成本可控 + 安全可控 + 能持续改进。  
> **基线日期：** 2026-08-30  
> **对照代码：** `src/agent/`、`src/tools/`、`src/web/`  
> **与计划书关系：** 本文是「质量视角」总纲；细节实现仍可回链计划书第 11～17 节。改完一项后在本文勾选，并视需要回写计划书进度日志。

**当前总判：** 作业级 / 答辩级已达标；离「严格好 Agent」还差一层——主要在决策机械性、成本硬闸、安全深度、离线评测闭环；**Capability 工具面已较齐（含 grep），缺口在 Search-first 行为、测试/git 一等工具、edit 护栏与能力 eval。**

**推进状态：** ① Capability **Cap-A/B/C 已落地**。② 决策合理 **Dec-A/B/C 已落地**。③ 成本可控 **Cost-A/B/C 已落地**（硬闸 + 可见/账单 + offline eval）；下一横切可转 **P1**（验证/安全）。

---

## 0. 怎么用这份文档

1. **先交付再大改**：Day4（视频 / zip）未完成前，不要开大坑。  
2. **有完整方法计划的维度（§2 Capability、§3 决策合理、§7 成本可控）**：严格按该节阶段执行；其余维度暂按第 10 节优先序。  
3. **每改一项必须有验收**：冒烟 +（更好）固定任务 eval 指标。  
4. **勾选约定**：`- [ ]` → `- [x]`；在「进度」小节补一行日期与结论。

---

## 1. 总览打分（基线）

| 维度 | 基线 | 一句话 |
|------|------|--------|
| 能力完整 | A- | Cap-A/B/C 齐：grep+护栏+run_tests/git+offline/live eval；缺 live steps 数字时先跑 `--live` |
| 决策合理 | A- | Dec-A/B/C 齐：cycle/BLOCK/停滞 + Skill + 离线 decision eval；live pathology 列已挂 |
| 上下文有效 | A- | 压缩/预算/Current State 扎实；长会话语义记忆仍弱 |
| 执行可靠 | B+ | 错误回传、配对修复、取消/steer 有；Web 审批 UX 不全 |
| 结果可验证 | A- | Evidence Gate + 测试解析是亮点；证据种类偏窄 |
| 成本可控 | A- | Cost-A/B/C 齐：硬闸 + 预算可见/cost_report + offline/live eval；基线见 §13 |
| 安全可控 | B+ | 沙箱/三级风险/敏感路径到位；无 OS 沙箱、链式 IFC、网络策略 |
| 持续改进 | C+ | transcript + 冒烟有；缺轨迹评测、失败归因、自动改 Skill |

**目标（交付后 1～2 周可冲）：** 决策 / 成本 / 持续改进各至少升半档；六项优先改造全部勾完。

---

## 2. 能力完整（Capability）— 调研结论与改造计划

> **本章目标：** 回答「Agent 到底能不能做」——不是模型聪不聪明，而是 **Harness 是否提供了完成编程任务所必需、且对模型友好的行动接口（ACI）**。  
> **调研日期：** 2026-08-30。先写计划，再按 §2.6 分阶段落地；勾选见 §2.5 / §2.6。

### 2.0 问题定义：Capability 评什么

```text
能力完整 ≠ 工具越多越好
能力完整 = 覆盖「定位 → 理解 → 修改 → 执行 → 验证」闭环
           + 每个动作对 LLM 友好（观测短、反馈明确、失败可恢复）
           + 过度能力可控（Least Privilege，见安全维）
```

**三类失败（来自 AgenTRIM 的 agency 叙事，能力侧对称）：**

| 类型 | 含义 | 本仓库例子 |
|------|------|------------|
| **Insufficient agency** | 缺关键动作，只能绕路烧步 | 无内容检索时只能 `list`+盲读；无测试摘要时靠裸 shell 猜 |
| **Awkward agency** | 有动作但 ACI 不友好 | 整文件读炸窗口；edit 无语法反馈；搜索结果过噪 |
| **Excessive agency** | 能力暴露过多 | 每步挂全工具表 / 危险 shell 常开（归安全维，Capability 只定「该有哪些」） |

**答辩金句：** 同一模型换 Harness，分数可差数个点；能力首先是 **工具面 + 观测格式** 的设计题（见 [Stop Comparing Agents Without Disclosing the Harness](https://arxiv.org/html/2605.23950v1)）。

---

### 2.1 资料与论文（精读清单）

| 来源 | 类型 | 核心主张 | 我们可偷什么 | 不要照搬 |
|------|------|----------|--------------|----------|
| **[SWE-agent / ACI](https://arxiv.org/abs/2405.15793)**（NeurIPS 2024） | 论文 | 专用 Agent-Computer Interface 远优于「裸 bash」；搜索结果要**压缩**；文件 viewer ~100 行；edit 后 **lint 护栏** | 搜索摘要格式；切片读默认窗口；edit 失败回滚式反馈 | 整套 CLI ACI、行号 edit（我们已用 str_replace） |
| **[OpenHands / CodeAct](https://arxiv.org/abs/2407.16741)** | 论文+平台 | 通用动作空间（bash/ipython/browser）+ AgentSkills 库补「bash 难做好」的事 | 「专用工具补 shell」原则；编辑器 skill 思路 | Docker 全栈、浏览器、Jupyter |
| **[Claude Code 能力分类](https://code.claude.com/docs/en/how-claude-code-works)** | 产品文档 | 五类能力：File / Search / Execution / Web / Code intelligence；Skill/MCP/Subagent 是扩展层 | 用五类做能力清单与缺口表 | MCP 生态、云端 VM、完整 code intelligence |
| **lean harness 观点**（[Ars：Beyond grep](https://arstechnica.com/ai/2026/07/beyond-grep-the-case-for-a-context-rich-ai-coding-harness/)） | 访谈 | 模型变快时 harness 宜瘦；grep 系 vs 语义检索是两条路 | **先把 grep 系做透**，语义 RAG 作补充而非替代导航 | 重型私有索引引擎 |
| **[AgenTRIM](https://arxiv.org/abs/2601.12449)** | 论文 | Excessive vs Insufficient agency；离线盘点工具面 + 在线按步过滤 | **能力清单要可盘点**；工具带 risk/readonly 元数据（已部分做） | 自动 extractor 全流水线 |
| **Progressive Disclosure**（Anthropic Skills / [简述](https://ardalis.com/optimizing-ai-agents-with-progressive-disclosure/)） | 工程范式 | L1 只暴露 name+description，正文按需加载 | Skill 已是能力扩展层；工具 description 当「选择器」写短 | 80K Skill 市场、向量 Router |
| **Harness 敏感评测**（[arxiv 2605.23950](https://arxiv.org/html/2605.23950v1)） | 立场文 | 分数是 model×harness 联合产物；搜索子代理等 scaffold 可单独抬分 | 改能力后必须 **固定模型** 做前后对比 | 追公开榜刷分 |
| **CORE / TRACE**（路径评测） | 评测框架 | 不止最终对错，还看路径效率/幻觉/适应性 | Capability eval 记 **步数、是否走 search-first** | 完整 DFA 标注成本过高则简化 |

**推荐阅读顺序（约 3～4 小时）：** Claude Code 能力表 → SWE-agent ACI 节（search/viewer/edit/lint）→ AgenTRIM §1–3（agency 平衡）→ 本仓工具对照表（§2.3）。

---

### 2.2 可落地方法库（方法 → 本仓映射）

> 下列方法是「调研后定案可做」的操作化条目，不是概念堆砌。

#### 方法 M1：五层能力地图（Capability Map）

按 Claude Code 五类盘点，缺哪补哪；每项标 **有 / 弱 / 无**。

| 层 | 含义 | 本仓库现状（2026-08-30） | 判定 |
|----|------|--------------------------|------|
| **File** | 读 / 写 / 精确改 / 列目录 | `read_file`（含 offset/limit）/ `write_file` / `edit_file`（str_replace）/ `list_dir` | ✅ 有 |
| **Search** | 按名找文件 + 按内容找行 | `glob` + **`grep`（已实现）**；无符号级 go-to-def | ✅ 有（弱：无符号索引） |
| **Execution** | 跑命令 / 测 / 构建 | `run_shell` + **`run_tests`（结构化）** + 只读 `git_status`/`git_diff` | ✅ Cap-B |
| **Verify 辅助** | 改后语法/类型反馈 | `.py` write/edit 经 `ast.parse`；非法语法不落盘 | ✅ Cap-A |
| **Orchestration** | 计划 / Skill / 记忆 / 子代理 | todo + 3 Skills + memory/rag；**无子 Agent** | ✅ 有（子代理缓做） |
| **Web / CI** | 查文档、外网 | 无（作业范围可永久不做） | ❌ 不做 |

#### 方法 M2：Search-first ACI（SWE-agent）

**原则：** 定位阶段默认 `grep`/`glob` → 切片 `read_file`，禁止「整仓 list + 全文 read」当主路径。

落地动作：

1. 保持 `grep` 观测 **短**：`path:line:preview`，限制 `max_matches`（已有）；可加「仅文件名列表」模式（SWE-agent `search_dir` 启示）。  
2. System / debugging Skill 写死顺序：报错字符串 / 符号名 → grep → offset/limit 读。  
3. Eval：陌生小任务「找到定义并说明」——有 grep 路径步数应显著更少。

#### 方法 M3：友好观测（Observation UX）

来自 SWE-agent：空输出明示成功；搜索过噪会害模型。

落地动作：

1. 空 stdout → 固定句：`Command succeeded with empty output.`（若 shell 尚未统一则补）。  
2. 大文件无 offset 时 **强提示**切片（已有 hint）；可升级为：超 N 行默认只返回头 100 行 + 提示（更接近 ACI viewer）。  
3. `grep` 截断时页脚说明 scanned files / hit cap（已有）。

#### 方法 M4：Edit Guardrail（语法护栏）

来自 SWE-agent lint-on-edit：语法坏的 edit **不落地**或立刻回滚并报错。

落地动作（本仓最小版）：

1. `edit_file` / `write_file` 后对 `.py` 跑 `ast.parse`；失败则恢复旧内容并 `Error: syntax ...`。  
2. 不引入完整 LSP（Code intelligence 标为 P2）。

#### 方法 M5：专用工具补 Shell（OpenHands Skills 原则）

「bash 能做 ≠ 该只用 bash」——高频、要结构化结果的动作应有一等工具。

| 候选工具 | 为何专用 | 优先级 |
|----------|----------|--------|
| `grep` | 已有 | ✅ |
| `run_tests`（或 `pytest` 包装） | 统一 exit + 失败摘要 → TaskState / Evidence | **P0 能力补强** |
| `git_status` / `git_diff`（只读） | 稳定 diff 观测，少解析噪声 shell | P1 |
| `ask_user` | 能力不足时主动降级（执行/决策交叉） | P2 |

#### 方法 M6：能力渐进披露（工具 + Skill）

- **工具 description**：当选择器（何时用 / 何时勿用），勿写成手册。  
- **Skill**：方法剧本扩展「会做一类任务」；不新增权限。  
- **可见集**（已有 `tool_visibility`）：按阶段收窄 = 能力按需出现，减轻 insufficient/excessive 两端。

#### 方法 M7：Capability 验收 = 固定任务 + 路径指标

不要只看「最终修好」：

| 指标 | 含义 |
|------|------|
| `task_success` | 测试绿 / 约定验收过 |
| `steps` | LLM 往返次数 |
| `search_first_rate` | 定位阶段是否先 grep/glob |
| `blind_full_read_count` | 无 offset 的大文件全文读次数（越低越好） |
| `tool_coverage` | 轨迹是否用到本应具备的工具（有 grep 却从不用 → 描述/Skill 问题） |

（与持续改进维 I1 eval 集打通；Capability 改完必须跑同一套。）

#### 方法 M8：能力边界清单（显式「不能做」）

写进 README/答辩，避免虚假能力：

- 无浏览器 / 无外网检索（除非日后加）。  
- 无 LSP 级引用跳转。  
- 无多 Agent 并行探索（C4 缓做）。  
- Windows 路径沙箱 ≠ OS 隔离（安全维）。

---

### 2.3 对照表：业界最小充分集 vs 本仓库

| 能力原子 | Claude Code / SWE-agent 共识 | 本仓库 | 差距动作 |
|----------|------------------------------|--------|----------|
| 读文件（可切片） | Read / viewer ~100 行 | ✅ `offset`/`limit` + ≥100 行 auto-head | — |
| 写 / 精确改 | Write + Edit（str_replace） | ✅ + `.py` ast 护栏 | — |
| 文件名搜索 | Glob / find_file | ✅ `glob` | — |
| 内容搜索 | Grep / search_dir | ✅ `grep` | Skill/prompt 强制 search-first；可选 compact 模式 |
| 执行命令 | Bash | ✅ `run_shell` | — |
| 跑测试（结构化） | 常靠 bash 或包装 | ✅ Cap-B：`run_tests` | — |
| 看改动 | git / diff 工具或 bash | ✅ Cap-B：只读 `git_status` / `git_diff` | — |
| 计划 | Todo | ✅ | — |
| 方法包 | Skills | ✅ 3 个 | 保持短正文 |
| 子代理 | 有 | ❌ | **C4 缓做** |
| 编辑后诊断 | lint / diagnostics | ✅ Cap-A：`ast.parse`（非 LSP） | LSP 仍缓做 |
| 语义检索 | 可选（Augment 路线） | 弱 TF–IDF RAG | 不替代 grep；维持补充 |

**结论修正（相对本文初版）：** C1「缺 grep」**代码侧已基本完成**；Capability 主缺口转为 **C3 测试一等工具、C5 edit 护栏、Search-first 行为固化、C2 只读 git**，以及 **用 eval 证明「能做且做得省」**。

---

### 2.4 已有（保持，勿回归）

- File：`read_file` / `write_file` / `edit_file` / `list_dir`  
- Search：`glob` / `grep`（含 max_matches、噪声目录过滤）  
- Execution：`run_shell` + **`run_tests`** + 沙箱 / 审批；只读 **`git_status` / `git_diff`**  
- Orchestration：`todo_write`、Skills、`load_skill`、memory/rag  
- 入口：CLI + Web；同一 `loop.py`  
- 元数据：`risk_level` / `is_readonly` + 阶段可见集  
- Cap-A：Search-first 规则、`.py` ast 护栏、≥100 行 auto-head  

---

### 2.5 缺口与改造项（Capability 清单）

| ID | 缺口 | 采用方法 | 建议动作 | 状态 |
|----|------|----------|----------|------|
| C1 | Search-first **行为**未钉死（工具已有） | M2, M6 | 更新 debugging/testing Skill + system 短规则；冒烟断言偏好 grep | [x] Cap-A |
| C2 | 无结构化只读 git | M5 | `git_status` / `git_diff`（只读、workdir 内、deny 写操作） | [x] Cap-B |
| C3 | 测试无专用工具 | M5, M7 | `run_tests`：跑 pytest/unittest，返回 exit + 失败摘要，写入 TaskState | [x] Cap-B |
| C4 | 无子 Agent | — | 有界委托；答辩提下一步 | [ ] 缓做 |
| C5 | 无 edit 语法护栏 | M4 | `.py` 写/改后 `ast.parse`，失败回滚 | [x] Cap-A |
| C6 | 大文件默认全文仍可能过胖 | M3 | 超 N 行且无 offset → 默认返回头 100 行 + 续读提示（可配置） | [x] Cap-A |
| C7 | Capability 无可比 eval | M7 | 与 I1 共用：定位任务 + greeter 修复；报 steps / search_first_rate | [x] Cap-C |
| C8 | 能力边界未写清 | M8 | README / 本文 §2.0 边界表进提交说明 | [x] Cap-C |

---

### 2.6 实施计划（按此执行）

```text
阶段 Cap-A（0.5～1 天）— 行为与护栏，不扩权限面
  1. C1 Search-first：Skill + system 三条规则
  2. C5 ast 护栏
  3. C6 可选默认头 N 行（若与现有 truncate 冲突，以「明示未读完」为准）
  4. 冒烟：grep 路径 / 坏语法 edit 被拒

阶段 Cap-B（0.5～1 天）— 专用执行工具
  1. C3 run_tests → 接 TaskState.test_status + CompletionGate
  2. C2 git_status / git_diff（只读）
  3. 冒烟 + 一次真实 greeter 对比步数

阶段 Cap-C（与 I1 合并，0.5 天）— 证明「能做」
  1. C7 最小 eval：至少 2 个任务（locate-string / fix-greeter）
  2. C8 文档边界
  3. 记录基线 steps → 本文 §13 进度表
```

**依赖与边界：**

- 不新开 MCP / 浏览器 / LSP。  
- 新工具必须：`risk_level` + Gate +（写类）审批策略。  
- `run_tests` 默认只跑 workdir 内用户指定或演示约定路径，禁止任意 `rm`。  
- Capability 补强若与安全冲突 → **安全优先**（可见集可暂时藏高危）。

---

### 2.7 验收标准（本章 Definition of Done）

- [x] Cap-A 完成：坏 Python edit 无法留下非法语法文件；Skill 含 search-first。  
- [x] Cap-B 完成：同一修复任务可只靠 `run_tests` 得到结构化失败摘要（不必手写解析 shell）。  
- [x] Cap-C：eval 套件落地（`locate-string` / `fix-greeter`）；离线路径进冒烟；live 可出 **steps** 基线。  
- [x] 冒烟覆盖新工具（Cap-A/B/C offline）；README 已写能力边界。  

**本章不做完不算 Capability 升级；仅有工具注册但无行为/eval → 仍算 Insufficient agency 风险。**

#### Cap-A Web 端印证（手动）

重启 Web 后 Ctrl+F5，工作区选 `demos`（或含测试的目录），用时间线核对下列三点：

1. **Search-first** — 提问：`在 demos 里用工具找到 greet 相关定义，先搜索再读文件，不要整目录乱翻。`  
   期望时间线先出现 `grep`/`glob`，再出现带 `offset`/`limit` 的 `read_file`（或长文件出现 `auto-head` 观测）。
2. **AST 护栏** — 提问：`请用 edit_file 把 greeter.py 改成故意语法错误的 Python（缺括号），看工具怎么回报。`  
   期望：步骤结果含 `Error: syntax rejected` / `NOT modified`；右侧 Files 打开该文件仍是合法代码。
3. **大文件 auto-head** — 若 demos 无 ≥100 行文件，可先让 Agent `write_file` 写一个合法的长 `.py`（≥100 行），再让它 `read_file` 不带 offset。  
   期望：工具结果含 `auto-head`、`lines 1-100 of …`，且提示 `offset=101` 续读。

#### Cap-B Web 端印证（手动）

重启 Web + Ctrl+F5，工作区选 **`demos`**（须能看到 `greeter_test.py`）：

1. **`run_tests` 结构化结果** — 提问：  
   `不要用 run_shell。请只用 run_tests，target=greeter_test.py，runner=python，把测试跑一遍并说明 passed/exit_code。`  
   期望时间线工具名为 `run_tests`；结果含 `# run_tests`、`passed: true|false`、`exit_code:`。
2. **失败摘要可进状态** — 若 greeter 当前是绿的，可先说：`先把 greeter.py 改错一处（保持语法合法），再用 run_tests 跑 greeter_test.py。`  
   期望：`passed: false` + 非零 exit；随后修好再跑应 `passed: true`。
3. **只读 git** — 提问：`用 git_status 看当前改动；若有变更再用 git_diff 看 diff。不要用 shell 跑 git。`  
   期望：时间线为 `git_status` / `git_diff`（若 demos 不是独立 git 根而挂在仓库内，应能出 status；若报 not a git repository 也属工具正常回报）。

#### Cap-C 如何测试

**1）离线（无 API，必跑）**

```powershell
conda activate codeagent
cd G:\codeagent
python -m scripts.run_capability_eval --offline
# 或整包冒烟（已含 offline eval）
python -m scripts.smoke_v1
```

期望：表里三行均为 `ok=Y`（score:search-first / score:blind-list / harness:fix-greeter-path）。

**2）在线 live（需 `.env` 里 `DEEPSEEK_API_KEY`，产出 steps 基线）**

```powershell
python -m scripts.run_capability_eval --live
# 或单任务：
python -m scripts.run_capability_eval --live --task locate-string
python -m scripts.run_capability_eval --live --task fix-greeter
```

看终端表：

| 列 | 含义 |
|----|------|
| `steps` | LLM 往返次数（记入 §13 基线） |
| `grep` / `search1st` | locate 任务应多为 Y |
| `run_tests` / `tests_green` | fix-greeter 应尽量 Y |
| `ok` | 任务是否达到硬门槛 |

JSON 默认写到 `evals/results/live_*.json`。把 `fix-greeter` 的 `steps` 抄到本文 §13。

**3）Web 对照（可选）**  
用与 eval prompt 相近的话术跑一遍，对照时间线是否出现 `grep` / `run_tests`（与 live 指标一致即可，不必数字相同）。

离线冒烟：`D:\ana\envs\codeagent\python.exe -m scripts.smoke_v1`（需 codeagent 环境）。

---


## 3. 决策合理（Decision）— 调研结论与改造计划

> **本章目标：** 回答「Agent 会不会选对下一步」——不是模型更聪明，而是 **Harness 在决策边界上能否：拦病理、逼换策、对齐目标、在预算内停**。  
> **调研日期：** 2026-08-30。先写计划，再按 §3.6 分阶段落地；勾选见 §3.5 / §3.6。  
> **与 Capability 关系：** §2 解决「能不能做」；§3 解决「会不会瞎做 / 死做」。同一模型换决策护栏，步数与成功率可差一截。

### 3.0 问题定义：Decision 评什么

```text
决策合理 ≠ 多想几步 / 多写几句 CoT
决策合理 = 每步动作相对目标有进展
           + 失败后换策略（而非重放同一 fingerprint）
           + 病理模式在 dispatch 边界被拦（不靠自觉）
           + 停因可分类：目标达成 / 资源耗尽 / 病理停 / 外部中断
```

**四类失败（对照 Anatomy of Termination + 本仓现象）：**

| 类型 | 含义 | 本仓库例子 |
|------|------|------------|
| **Myopic / ungrounded** | 想下一步却与真实状态/目标脱节 | 已测绿仍瞎改；Current State 有失败策略仍重试同参 |
| **Strategy lock-in** | 同一失败策略反复执行 | `edit` 同路径同错；Retry nudge 被模型无视 |
| **Oscillation** | A↔B 周期 / ping-pong，无新信息 | `read_file` ↔ `run_tests` 交替烧步 |
| **False progress** | 在动但信息不增 / 观测不变 | 重复读未改文件；模糊改参绕过 exact dedup |

**答辩金句：** 生产 Agent 的决策质量 = **推理骨架（ReAct/ReflAct）× 病理检测（Stuck）× 硬边界换策（Debounce/Blacklist）× 终止分类**；只靠 prompt「请勿重复」不算决策升级。

---

### 3.1 资料与论文（精读清单）

| 来源 | 类型 | 核心主张 | 我们可偷什么 | 不要照搬 |
|------|------|----------|--------------|----------|
| **[ReAct](https://arxiv.org/abs/2210.03629)**（ICLR 2023） | 论文 | Thought↔Act↔Obs 交织；短视但可落地 | 保持单环；决策护栏挂在 Act 前后 | 纯文本环境假设 |
| **[Reflexion](https://arxiv.org/abs/2303.11366)** | 论文 | 失败 → 自然语言反思进记忆 → 下轮换策 | **失败策略写入 TaskState / Current State**（已部分做） | 每轮再调一个 Reflector LLM |
| **[Honest Lying](https://arxiv.org/html/2605.29463)** | 论文 | 自诊断反思可 confabulate，固化错误信念 | 反思要用 **程序化失败信号**（failure_key），少靠模型自述病因 | 完整 MAR 多批评家 |
| **[ReflAct](https://arxiv.org/abs/2505.15182)**（EMNLP 2025） | 论文 | 每步先「状态相对目标」再动作；优于 ReAct+Reflexion 外挂 | **强化 Current State / goal 对齐**（短规则：先看状态再选工具） | 换整套 prompting 骨架、ALFWorld 设定 |
| **[AdaPlanner](https://arxiv.org/abs/2305.16653)**（NeurIPS 2023） | 论文 | 闭环：in-plan 细化 + out-of-plan 改计划 | 失败后 **改 todo / 换路径**，而非同参重放 | 代码式整计划、ask_LLM 动作 |
| **[OpenHands StuckDetector](https://docs.openhands.dev/sdk/guides/agent-stuck-detector)** | 产品/工程 | 五种 stuck：同 AO 重复、同 AE 重复、独白、**交替 ping-pong**、上下文错误环 | **D1 直接对标**：滑动窗口语义比较 action/obs | 整套 event stream / LoopRecovery UX |
| **[AWS Strands Debounce / LimitToolCounts](https://dev.to/aws/how-to-prevent-ai-agent-reasoning-loops-from-wasting-tokens-2652)** | 工程范式 | `BeforeToolCall` 硬 `cancel_tool`；重复参 BLOCK；工具次数硬顶 | **D3：dispatch 边界硬 BLOCK + 写回 tool 消息** | 绑死 Strands API |
| **[Anatomy of Termination](https://towardsai.com/p/machine-learning/when-should-an-agent-stop-the-anatomy-of-termination)**（Towards AI） | 工程综述 | 停因四类：Goal / Resource / Pathology / External；retry 分 transient vs strategy | 统一 `stopped_reason`；病理停早于 `max_steps` | 重写整层 termination 框架 |
| **[Bounded agentic loop](https://aiarch.dev/patterns/bounded-agentic-loop)** | 模式文 | 预算 + 进度检测 + kill switch；三出口 | 与成本维 $1 对齐：无进展 = 病理 | 网关级 spend cap |
| **Claude Code Plan Mode**（产品） | 产品 | 复杂改动先 plan 再执行，人可审 | D4 软→硬：粗 todo 后再批改；勿默认强制 plan | 完整人审 UX（作业可软约束） |
| **ReCAP / ReAcTree / HiMAC** 等层级规划 | 论文族 | 长程任务需层次分解，防线性漂移 | 答辩「下一步」：子目标树；**本仓缓做** | 递归多 Agent / 行为树 |

**推荐阅读顺序（约 3～4 小时）：** Anatomy of Termination（停因分类）→ OpenHands StuckDetector 五模式 → AWS Debounce 硬 BLOCK → ReflAct 摘要（goal-state）→ Reflexion + Honest Lying（反思陷阱）→ 对照本仓 `loop_guard` / `retry_policy`。

---

### 3.2 可落地方法库（方法 → 本仓映射）

> 下列方法是「调研后定案可做」的操作化条目；原则与 §12 一致：**检测与拦截放在 dispatch 边界**。

#### 方法 M-D1：病理分类器（Stuck Taxonomy）

对齐 OpenHands 五模式，压缩为本仓三级：

| 级别 | 检测 | 动作 |
|------|------|------|
| L1 Exact streak | 同 fingerprint 连续（已有 LoopGuard） | warn → `loop_detected` stop |
| L2 Cycle / ping-pong | 滑动窗口内长度 2～4 的交替模式（如 A-B-A-B） | warn 注入 → 再犯 stop |
| L3 Stagnation | 观测指纹不变 / 无新文件·测试状态变化 | nudge → 可选 stop |

**落地：** 扩展 `LoopGuard`（或旁路 `StuckDetector`），对 **action 指纹序列** 做 cycle 检测；obs 侧先做「结果哈希」简版（D2）。

#### 方法 M-D2：策略黑名单硬 BLOCK（Strategy Blacklist）

来自 Strands Debounce + 本仓 RetryPolicy：

1. `failure_key` / fingerprint 进入 `failed_strategies` 后，**同 fingerprint 再次 dispatch → 不执行**，返回固定 tool 错误：`BLOCKED: strategy exhausted; change args or tool`。  
2. BLOCK **必须**写 `role=tool`（保 pairing）。  
3. 与 `retry_exhausted` 对齐：耗尽后可停环，或仅 BLOCK 并允许模型换 fingerprint 继续（推荐：**先 BLOCK 换策，连续 N 次无新 fingerprint 再停**）。

#### 方法 M-D3：Goal–State 短反射（ReflAct 精简版）

不换整骨架，只强化已有 Current State：

1. System / 注入块固定三问：目标？当前证据（测试/diff）？已失败策略？  
2. 每步工具选择前，模型被约束「优先处理未完成 todo / 未验证断言」。  
3. **禁止**再开独立 Reflector LLM（成本与 confabulation 风险；见 Honest Lying）。

#### 方法 M-D4：Retry 分层（Termination 里的四种 retry）

| 类型 | 谁处理 | 本仓策略 |
|------|--------|----------|
| Transient（网络/429） | 基础设施 | API 层有限退避；**不**进 failure_key |
| Format（JSON/参数坏） | harness | 回错让模型改格式 |
| Semantic（工具 ok 但结果不对） | 模型换策 | 进 failure_key |
| Strategy（同参同错） | **硬 BLOCK** | M-D2 |

#### 方法 M-D5：Plan-then-Act 软门槛（AdaPlanner / Plan Mode 精简）

- 多文件 / 不明修复：先 `todo_write` 粗计划再改代码（Skill + 可见集可在 `planning` 阶段藏 write）。  
- **不**默认强制 schema「无 todo 禁 write」（易误伤小任务）；Eval 上对复杂任务记 `plan_first_rate`。

#### 方法 M-D6：进度信号（False Progress 对抗）

与成本/上下文交叉：

- 观测指纹：`hash(tool_name + 截断后结果)`；连续相同 → stagnation。  
- TaskState 变更：`test_status` / todo 勾选 / 目标文件 mtime 无变化计数。  
- 与 X3 soft-dedup、C6 auto-head 配合，减少「假动作」。

#### 方法 M-D7：终止原因可观测

每个 run 必须有单一主因（可附次因）：

`completed` | `goal_met_forced` | `max_steps` | `budget_exhausted` | `loop_detected` | `cycle_detected` | `retry_exhausted` | `interrupted`

Eval 报：病理停是否 **早于** `max_steps`；误伤率（本可完成却被 cycle 掐死）。

#### 方法 M-D8：决策验收指标

| 指标 | 含义 |
|------|------|
| `task_success` | 与 Capability 共用 |
| `steps` | 越低越好（在成功前提下） |
| `cycle_events` | 检测到的 ping-pong / cycle 次数 |
| `blocked_replays` | 硬 BLOCK 次数（应 >0 在故意复现轨迹上） |
| `pathology_stop_rate` | 病理停占比；应在合成转圈任务上高、正常 greeter 上≈0 |
| `strategy_switch_rate` | BLOCK 后下一步是否换了 fingerprint |

---

### 3.3 对照表：业界决策护栏 vs 本仓库

| 决策原子 | 业界共识 | 本仓库 | 差距动作 |
|----------|----------|--------|----------|
| Exact 重复拦 | Stuck / Debounce | ✅ LoopGuard streak warn/stop | — |
| 同轮 dedup | 常见 | ✅ same-step cache | — |
| 失败策略记忆 | Reflexion 式 | ✅ RetryPolicy + TaskState | — |
| 失败策略 **硬拦** | Strands cancel_tool | ⚠️ 偏 nudge / 软文案 | **D3** |
| A↔B cycle | OpenHands alternating | ❌ | **D1** |
| 观测停滞 | stagnation / novelty | ❌ / 弱 | **D2** |
| Goal–state 对齐 | ReflAct | ✅ Current State；规则可再钉 | Dec-A 短规则 |
| Retry 分层 | Anatomy 四类 | ⚠️ 未严格分 transient | **D3 + E3 交叉** |
| Plan-then-act | Claude Plan / AdaPlanner | 软（todo Skill） | **D4** 评估 |
| 病理停早于步数顶 | Bounded loop | 部分（exact only） | D1 后补齐 |
| 子目标树 | ReAcTree 等 | ❌ | 缓做 |

**结论：** 决策维 **Dec-A/B/C 已闭环**（护栏 + Skill + 离线 eval + live pathology 列）；横切下一优先 **§7 成本硬闸（P0-3 / Cost-A）**。

---

### 3.4 已有（保持，勿回归）

- `LoopGuard`：同轮 dedup、连续 exact ≥3 warn / ≥5 stop、error-streak nudge  
- `RetryPolicy`：failure_key 阶梯 → `retry_exhausted`；镜像进 TaskState  
- `TaskState` + Current State 注入（含失败策略）  
- `tool_visibility` 按阶段收窄  
- Skill 关键词预注入；CompletionGate / 多类 `stopped_reason`  
- dispatch 边界已能改写 tool 结果（dedup reuse 路径）——**硬 BLOCK 应走同一通道**

---

### 3.5 缺口与改造项（Decision 清单）

| ID | 缺口 | 采用方法 | 建议动作 | 状态 |
|----|------|----------|----------|------|
| D1 | A↔B 周期 / ping-pong 未拦 | M-D1, M-D7 | 滑动窗口 cycle（窗长 2～4，重复 ≥2～3 轮）；warn→`cycle_detected` stop | [x] Dec-A |
| D2 | 输出停滞 / 模糊相似 args | M-D1, M-D6 | 观测哈希不变计数；默认只 warn（`LOOP_STAGNATION_STOP_AFTER=0`） | [x] Dec-B |
| D3 | Retry 靠自然语言，可被无视 | M-D2, M-D4 | dispatch 对黑名单 fingerprint **硬 BLOCK** + tool 回写；连续无新策略再 `retry_exhausted` | [x] Dec-A |
| D4 | 批工具 / 粗 todo 仅 prompt 软约束 | M-D5 | 复杂任务 Skill 强调 plan-then-act；**不做**强制 schema | [x] Dec-B |
| D5 | 任务路由偏关键词 | — | Skill 增多后再轻量 router | [ ] 缓做 |
| D6 | Goal–state 易漂 | M-D3 | system 三条：先读 Current State → 禁复述已失败 fingerprint → 无证据不宣告完成 | [x] Dec-A |
| D7 | 决策维无可比 eval | M-D8 | `evals/decision.py` + `run_decision_eval --offline`；live 表加 pathology 列 | [x] Dec-C |

### 3.6 实施计划（按此执行）

```text
阶段 Dec-A（0.5～1 天）— 硬边界：cycle + BLOCK
  1. D1：LoopGuard/Stuck 增加 alternating cycle 检测；stopped_reason=cycle_detected
  2. D3：RetryPolicy 黑名单在 dispatch 硬 BLOCK（写 role=tool）
  3. D6：system / Current State 三条短规则
  4. 单测：构造 A-B-A-B 与同 fingerprint 第四次 → 期望 BLOCK/停，且早于 max_steps

阶段 Dec-B（0.5 天）— 停滞与误伤校准
  1. D2：观测指纹停滞 nudge（先 warn，阈值保守）
  2. 正常 greeter / locate live：pathology_stop_rate ≈ 0（防误伤）
  3. D4：仅 Skill/文档层 plan-then-act；不做强制 schema

阶段 Dec-C（与 I1 合并，0.5 天）— 证明「决策更省更稳」
  1. D7：eval 增加决策列或独立 decision smoke
  2. 对比 Dec 前：故意转圈任务应更早停；fix-greeter steps 不显著变差
  3. 记录基线 → §13
```

**依赖与边界：**

- 不引入第二模型做 Reflector；不引入多 Agent 辩论（MAR）。  
- BLOCK / cycle stop 不得破坏 tool pairing。  
- 阈值宁松勿紧：先 warn 再 stop；误伤用 greeter live 回归。  
- 与成本维 $1、执行维 E3（transient vs strategy）共享术语；实现可同 PR 或紧随。  
- 决策护栏若与「用户明确要求重复跑同一命令」冲突 → 尊重用户高优先级指令或审批覆盖（文档写明）。

---

### 3.7 验收标准（本章 Definition of Done）

- [x] Dec-A：合成 `read↔run_tests`（或等价）交替轨迹 → warn 后 `cycle_detected`，步数 < `max_steps`。（smoke：`LoopGuard` A↔B）  
- [x] Dec-A：同 failure fingerprint 在耗尽后再次调用 → **不执行**工具，tool 消息含 `BLOCKED`。（smoke + loop dispatch）  
- [x] Dec-B：观测停滞默认只 warn（`stag_stop=0`）；Skill 含 plan-then-act；Web greeter 防误伤已由 Dec-A 核对通过。  
- [x] Dec-C：`python -m scripts.run_decision_eval --offline` 四行全 Y；smoke 已挂决策 fixtures；§13 有记录。  

**本章不做完不算决策升级；仅有更长 prompt「请换策略」→ 仍算 Strategy lock-in 风险。**

#### 决策维如何测试（Dec-A/B/C）

**1）离线（无 API，必跑）**

```powershell
conda activate codeagent
cd G:\codeagent
python -m scripts.smoke_v1
python -m scripts.run_decision_eval --offline
python -m scripts.check_dec_a
```

期望：

| 命令 | 期望 |
|------|------|
| `smoke_v1` | 结尾 `OK`（含 Cap-C + Dec-C fixtures） |
| `run_decision_eval --offline` | 四行 `ok=Y`：`cycle-stop`（steps=6, early=Y）、`block`、`stagnation-warn`、`no-false-cycle` |
| `check_dec_a` | 含 cycle / BLOCK / STAGNATION / Dec-C 四行并以 `OK` 结束 |

**2）Web 防误伤（可选）**

正常 greeter 修复不应出现 `cycle_detected` / `strategy_blocked` / `stagnation_detected`。  
live Capability 表新增 `pathology` 列：正常任务应为 `0`。

### 3.8 与计划书

- 细节见计划书 **第 16 节**（P2：cycle / soft-dedup）；本文 §3 为质量视角方法总纲，落地以 §3.6 为准。

---

## 4. 上下文有效

### 4.1 已有（保持）

- 工具输出截断、`trim_messages`、ACON 风格 Context Manager、`CONTEXT_TOKEN_BUDGET`  
- TaskState / working_memory / turn summary  
- system 前缀稳定（利缓存）；Web 上下文剩余条

### 4.2 缺口与改造方向

| ID | 缺口 | 建议动作 | 调研关键词 | 状态 |
|----|------|----------|------------|------|
| X1 | 压缩是否丢关键信息不可测 | 抽检：压缩前后关键路径/断言是否仍在 | context compaction eval, ACON | [ ] |
| X2 | 跨 run 语义记忆弱 | 结构化 episode / 失败策略库；先规则后向量 | agent memory episodes, working memory | [ ] |
| X3 | 同文件未变仍重读全文 | soft-dedup：mtime 未变则回摘要 | soft dedup tool result, Kimi tool-dedup | [ ] |

### 4.3 与计划书

- 细节见计划书 **第 11 节**；与 15/16 交叉见 soft-dedup。

### 4.4 验收

- 超预算长对话不炸；关键文件名/测试名压缩后仍可被模型引用。  
- 连续两次 `read_file` 同路径且未改文件，第二次 token 明显更少。

---

## 5. 执行可靠

### 5.1 已有（保持）

- 工具错误字符串回传；`sanitize_tool_pairing`  
- 终止：`completed` / `max_steps` / `interrupted` / `loop_detected` / `retry_exhausted` / `goal_met_forced`  
- 取消（`cancel`）与 steer；transcript 落盘

### 5.2 缺口与改造方向

| ID | 缺口 | 建议动作 | 调研关键词 | 状态 |
|----|------|----------|------------|------|
| E1 | Web 无 Confirm 弹窗 | High 目前一律 Deny；补会话内审批 UX | human in the loop approval UX | [ ] |
| E2 | exit 0 ≠ 语义成功 | 统一测试摘要解析；未跑到目标测不算绿 | test status parsing, false green | [ ] |
| E3 | transient vs strategy 重试未分层清 | API/锁类有限自动重试；策略失败只换策不重放 | retry taxonomy transient semantic | [ ] |

### 5.3 验收

- Web ASK 下 Medium/High 可点允许/拒绝，拒绝写回 tool 错误且不执行。  
- 只跑无关命令 exit 0 时，CompletionGate 不得放行「已修复」。

---

## 6. 结果可验证

### 6.1 已有（保持）

- `completion_gate.py` / `stop_conditions.py`  
- 测试全绿 / todo 完成催 FINAL；无证据拒空口完成  
- TaskState.`test_status` 解析 pytest 类输出

### 6.2 缺口与改造方向

| ID | 缺口 | 建议动作 | 调研关键词 | 状态 |
|----|------|----------|------------|------|
| V1 | 证据种类偏窄 | Mustlist：pytest 绿 +（可选）diff 非空 + 目标文件变更 | completion criteria evidence gate | [ ] |
| V2 | 假绿 / 改测试骗过 | 检测是否改了 `*_test.py` 本身；要求跑到约定用例 | verifiable completion, mustlist | [ ] |
| V3 | 开放任务缺 acceptance | 任务开始写显式 StopCondition（配置或首轮推断） | stop conditions agent patterns | [ ] |

### 6.3 与计划书

- 细节见计划书 **第 17.6～17.8 节**（Verification / Evidence）。

### 6.4 验收

- 模型未跑测就宣称完成 → 被 Gate 拦截并注入「请先跑测试」。  
- 仅改测试文件使套件变绿 → 应告警或拒绝（若启用 V2）。

---

## 7. 成本可控（Cost）— 调研结论与改造计划

> **本章目标：** 回答「Agent 会不会把钱/配额烧光」——不是模型更省，而是 **Harness 能否：在 dispatch 前硬闸、让模型感知剩余预算、把每分钱归因到步/工具、在无进展时早停**。  
> **调研日期：** 2026-08-30。先写计划，再按 §7.6 分阶段落地；勾选见 §7.5 / §7.6。  
> **与上下游关系：** §2/§3/计划书 §15 已把「正常路径少步、病理早停」做了一截；§7 补齐 **任务级累计资源硬闸 + 可观测复盘**（P0-3）。多模型路由/级联属答辩「下一步」，本仓单模型作业不强制。

### 7.0 问题定义：Cost 评什么

```text
成本可控 ≠ 上下文压缩做得好看 / Web 上有个圆环
成本可控 = 任务级「步数 × 累计 token（可估费用）」有硬上限
           + 闸在「下一 LLM / 下一副作用」之前同步检查（非事后告警）
           + 停因可分类：budget_exhausted ≠ max_steps ≠ 病理停
           + 每轮可复盘：哪几步、哪类工具吃掉预算
           + （可选）剩余预算可见 → 模型收紧探索（Budget Awareness）
```

**四类失败（对照 Resource Termination + 生产事故叙事）：**

| 类型 | 含义 | 本仓库例子 |
|------|------|------------|
| **Unbounded run** | 只有软提示或事后看板，环仍继续打 API | 有 `context_usage` 条，但无任务累计 token 硬停 |
| **Myopic budget** | 只限单步窗口 / 只限 `max_steps`，不管「步×窗」乘积 | 步数未满但每步 30k tok 已烧穿；或步数顶很晚、病理已空转 |
| **Blind agent** | 模型不知道还剩多少预算，临终仍广搜 | Current State 无 remaining tokens/steps |
| **Unattributed spend** | 知道贵，不知道贵在哪 | transcript 无 per-step / per-tool 汇总；无法对比改前改后 |

**答辩金句：** 生产 Agent 的成本质量 = **多维硬闸（steps/tokens/usd/tools）× 同步 reserve-or-deny × 预算可见 × 归因看板**；「看板红了但下一调用已发出」= 未实现成本可控（见 Agent Budget / Budget Controls 共识）。

**成本公式（本仓心智模型，对齐计划书 §15）：**

```text
任务成本 ≈ Σ_step ( 输入窗 token + 输出 token ) + 工具墙钟
         ≈ 步数 × 平均每步窗口大小   （单模型、无独立工具计费时）
```

因此：§2 Search-first / §3 病理早停 / 计划书批工具 → 砍**步数与假动作**；§4 压缩与可见集 → 砍**每步窗口**；§7 → 给乘积加**天花板与账单**。

---

### 7.1 资料与论文（精读清单）

| 来源 | 类型 | 核心主张 | 我们可偷什么 | 不要照搬 |
|------|------|----------|--------------|----------|
| **[FrugalGPT](https://arxiv.org/abs/2305.05176)**（Chen et al., 2023） | 论文 | 三维省钱：prompt 适配 / 近似模型 / **LLM cascade**；预算约束下选 API 序列 | 「级联=便宜模型先试 + 可靠则停」叙事；答辩对比单模型硬闸 | 多 API 编排、学阈值打分器（作业单 DeepSeek） |
| **[BATS / Budget-Aware Tool-Use](https://arxiv.org/abs/2511.17006)**（2025） | 论文 | **只加大 tool budget 不够**；需 Budget Tracker 持续告知剩余资源，再决定 dig vs pivot | **M-$3：Current State 注入 remaining steps/tokens** | 完整 BATS 规划/验证双模块、网页搜索设定 |
| **[Spend Less, Reason Better / BAVT](https://arxiv.org/abs/2603.12634)**（2026） | 论文 | 预算条件树搜索：剩余比例调节探索→利用；残差价值剪枝无信息工具调用 | 「低预算应收紧探索」原则；答辩「智能用预算 > 蛮力加步数」 | 训练-free 树搜索整框架（过重） |
| **[Token Economics for LLM Agents](https://arxiv.org/html/2605.09104v1)**（综述） | 综述 | Token 作生产要素；微观单 Agent 预算约束下的要素替代 | 统一成本度量（token+tool）；安全/协作属外圈 | 可微 token 市场、多 Agent 机制设计 |
| **PILOT / WISERouter / SATER**（EMNLP Findings 等） | 论文族 | 负载级预算下的 routing / cascade；校准置信再升级 | 答辩「多模型路由」路线图 | bandit 路由器、在线 LP（本仓无多模型） |
| **[UCCI](https://arxiv.org/html/2605.18796v1) / AutoMix / RouteLLM** | 论文 | cascade 关键是 **校准后的 verifier**；裸置信不可靠 | 若日后 cascade：用可程序化验收（pytest 绿）当「升级闸」而非模型自报信心 | 为作业训练校准头 |
| **Agent Budgets & Runaway Prevention**（[Jatin Bansal](https://jatinbansal.com/ai-engineering/agent-budgets-and-runaway-prevention/)） | 工程 | 预算=请求路径上同步谓词；告警≠强制；停时持久化部分状态 | **闸在下一步副作用之前**；多谓词 OR；`budget_exhausted` 一等出口 | 美元级事故 playbook 全文 |
| **Budget Controls**（[Agent Patterns](https://www.agentpatterns.tech/en/governance/budget-controls)） | 模式 | 最小集：`max_steps` / `max_seconds` / `max_tool_calls` / `max_usd`；每步 allow/stop + 审计 | **多维预算表**；显式 stop reason；审计日志 | 外部 PagerDuty |
| **Agent Budget Protocol RFC**（[reserve/commit](https://github.com/iamapsrajput/agent-budget-protocol/blob/main/RFC.md)） | RFC | 调用前按 worst-case **reserve**，回来后 commit；防并行超卖 | 单线程环：调用前用「本步估价」预检剩余即可（轻量版） | 分布式预算权威服务 |
| **Prompt caching 实践**（[llmbestpractices Cost Control](https://llmbestpractices.com/ai-agents/cost-control)；生产 77% 输入降本案例） | 工程 | 稳定前缀在前、可变后缀在后；测 cache hit；工具结果 memo | **保持** system/tools 稳定前缀（已有）；复盘可记 cache hint | 绑死 Anthropic TTL API |
| **Anatomy of Termination / Bounded agentic loop** | 工程综述 | Resource 停是四类停因之一；无进展=病理/资源交叉 | 与 §3 `stopped_reason` 对齐：`budget_exhausted` | 网关级 spend cap 产品 |
| **计划书第 15 节** | 本仓 | 贵的是「往返 × 窗口」；批工具/粗 todo 砍步数 | Cost 与 15 节共享指标；硬闸是 15 的天花板 | — |

**推荐阅读顺序（约 3～4 小时）：** Agent Patterns Budget Controls（多维闸）→ Bansal Agent Budgets（同步强制）→ BATS Budget Tracker 摘要 → FrugalGPT 三策略（知边界）→ 计划书 §15 → 对照本仓 `loop.py` / `context_usage` / `max_steps`。

---

### 7.2 可落地方法库（方法 → 本仓映射）

> 下列方法是「调研后定案可做」的操作化条目；原则与 §12 一致：**检测与拦截放在 dispatch / LLM 调用边界**。

#### 方法 M-$1：多维任务预算（Budget Envelope）

对齐 Agent Patterns 最小集，本仓压缩为可配置信封：

| 维度 | 配置（建议名） | 语义 | 优先级 |
|------|----------------|------|--------|
| 步数 | 已有 `max_steps` | LLM 往返上限 | ✅ 已有 |
| 累计 token | `MAX_TASK_TOKENS`（估） | Σ 每步 `estimate_messages_tokens(准备后窗口) + 输出估` | **P0** |
| 工具次数 | `MAX_TOOL_CALLS`（可选） | 防工具刷屏 | P1 |
| 墙钟 | `MAX_TASK_SECONDS`（可选） | 防挂死 | P1 / 缓 |
| 费用 | `MAX_TASK_USD` | 需价目表；单模型作业可先用 token×单价粗估 | P2 |

**硬规则：** 任一维度触顶 → 不再发起下一 LLM 调用 / 不再 dispatch 新副作用；`stopped_reason=budget_exhausted`（可附 `budget_kind=tokens|steps|tools|usd`）。

#### 方法 M-$2：同步闸（Sync Gate，非事后看板）

1. **检查点 A（LLM 前）：** `projected = cumulative + estimate(next_prompt)`；若 `projected > MAX_TASK_TOKENS` → 停，不调用 API。  
2. **检查点 B（工具前，可选）：** 工具次数 / 墙钟；与 PermissionGate 同层或紧邻。  
3. **记账点（LLM/工具后）：** 累加真实/估计用量；写入 run 状态与 SSE。  
4. **禁止：** 只靠 Web 圆环变红、或只靠 prompt「请节约」。

（完整 reserve/commit 协议过重；单线程 loop 用「预估预检 + 事后累加」即可。）

#### 方法 M-$3：预算可见（Budget Awareness，BATS 精简）

把剩余资源写进 **Current State / 系统短块**（不新开 Reflector LLM）：

```text
Budget: steps 3/12 | tokens ≈ 18k/50k (36%) | level=ok|warn|critical
```

- `warn` 建议阈值：剩余 ≤20% 或已用 ≥80%。  
- 文案约束：临近耗尽时优先验证/收束，勿新开大范围探索（与 §3 Goal–state、§2 Search-first 一致）。  
- **禁止**再调一个模型专门「估 ROI」（成本与 confabulation；对照 Honest Lying）。

#### 方法 M-$4：无进展早停（ROI / Progress Gate）

与 §3 M-D6 / 病理停交叉，成本视角再钉一条：

- 连续 N 步无 TaskState 进展（测试状态 / 目标文件 mtime / todo 勾选无变）且已消耗 ≥P% 预算 → 可 `budget_exhausted` 或沿用 `stagnation`/`cycle`（**优先病理原因**，预算作并列硬顶）。  
- 有进展则允许用满预算（避免误伤正常长修复）。

#### 方法 M-$5：成本归因（Cost Observability）

每轮结束（及 SSE 可选实时）输出结构化汇总：

| 字段 | 含义 |
|------|------|
| `steps` | LLM 往返 |
| `tokens_in_est` / `tokens_out_est` / `tokens_total_est` | 粗估（现有 `estimate_*`） |
| `peak_context_tokens` | 单步窗口峰值（已有 context 条可复用） |
| `tool_counts` | 按工具名计数 |
| `stopped_reason` / `budget_kind` | 停因 |
| `compress_events` | 折叠/截断次数（可选） |

落点：`result.memory["cost_report"]` + transcript + Web FINAL 脚注增强（计划书已有简易面板则扩展字段）。**不是**接入 Langfuse SaaS（作业范围）；接口形状对齐即可。

#### 方法 M-$6：省钱杠杆分层（知可为 / 知缓做）

| 层 | 手段 | 本仓态度 |
|----|------|----------|
| L0 硬闸 | M-$1/$2 | **Cost-A 必做** |
| L1 少步 | 批工具、Search-first、病理 BLOCK、粗 todo | **已大部分落地**（§2/§3/计划书15）；Cost 只验收不重复造 |
| L2 瘦窗 | 截断、可见集、auto-head、soft-dedup | 已有 + X3 待做 |
| L3 缓存 | 稳定 system 前缀 + cache hint | **保持**；测 hit 属加分 |
| L4 路由/级联 | FrugalGPT / RouteLLM / 三档模型 | **缓做**；验收用 pytest 当 verifier 叙事 |
| L5 树搜索 | BAVT | **不做** |

#### 方法 M-$7：成本验收指标

| 指标 | 含义 |
|------|------|
| `task_success` | 与 Cap/Dec 共用 |
| `steps` | 成功前提下越低越好 |
| `tokens_total_est` | 任务累计粗估 |
| `tokens_per_success` | 成功任务均值（主 KPI） |
| `budget_stop_early` | 人为低压预算时是否 **早于** 胡言/空转出现 `budget_exhausted` |
| `false_budget_kill` | 正常 greeter 在默认预算下被误杀率（应 ≈0） |
| `cost_report_present` | transcript/memory 是否含汇总 |

---

### 7.3 对照表：业界成本控制 vs 本仓库

| 成本原子 | 业界共识 | 本仓库 | 差距动作 |
|----------|----------|--------|----------|
| 步数硬顶 | `max_steps` | ✅ | — |
| 任务累计 token 硬顶 | Budget Envelope | ✅ Cost-A：`MAX_TASK_TOKENS` | — |
| 同步闸在 LLM 前 | Sync Gate / reserve | ✅ Cost-A：`TaskBudget.check_before_llm` | — |
| `budget_exhausted` 停因 | Resource Termination | ✅ Cost-A | — |
| 预算可见 | BATS Tracker | ✅ Cost-B：Current State `Budget:` 行 + SSE `task_budget` | — |
| 多维：tools/秒/usd | Agent Patterns | ❌ | **$5 可选** |
| 无进展×预算交叉 | ROI / Bounded loop | 部分（病理停） | **$2 与 Dec 共用** |
| 步数效率（正常路径） | 批工具 / ACI | ✅ Cap+计划书15 | 保持 |
| 病理早停 | Stuck/BLOCK | ✅ Dec-A/B | 保持 |
| 成本归因报告 | Langfuse 类 | ✅ Cost-B：`cost_report` → memory/transcript/Web FINAL | — |
| Prompt cache 布局 | 稳定前缀 | ✅ | 保持 |
| 模型级联/路由 | FrugalGPT 等 | ❌ 单模型 | **缓做** |

**结论：** 成本维 **Cost-A/B/C 已闭环**（硬闸 + 可见/账单 + offline/live eval）；P0-3 完成。

---

### 7.4 已有（保持，勿回归）

- `max_steps` → `stopped_reason=max_steps`  
- **Cost-A：** `MAX_TASK_TOKENS` / `--max-task-tokens`（默认 0=关）→ 累计估 token 硬闸 → `budget_exhausted`  
- **Cost-B：** Current State 预算行；`cost_report`；≤20% `[budget_warn]`；Web 顶栏/FINAL 工具摘要  
- **Cost-C：** `evals/cost.py` + `scripts/run_cost_eval.py`（offline/live）  
- `CONTEXT_TOKEN_BUDGET` + ContextManager 压缩/折叠 + Web 剩余% 条  
- 工具输出截断；`tool_visibility` 减 schema；批工具 / 粗 todo prompt  
- LoopGuard / Retry BLOCK / cycle / stagnation（病理向省钱）  
- Search-first、auto-head、`.py` ast 护栏（少无效读写）  
- 稳定 system 前缀 + `cache_policy` hint  
- Web 顶栏 steps + ≈used/budget tok；FINAL 脚注粗估（非 API 账单）  
- `estimate_tokens` / `estimate_messages_tokens`（可复用为任务累计）

---

### 7.5 缺口与改造项（Cost 清单）

| ID | 缺口 | 采用方法 | 建议动作 | 状态 |
|----|------|----------|----------|------|
| $1 | 无任务级累计 token 硬上限 | M-$1, M-$2 | `MAX_TASK_TOKENS`；LLM 调用前预检；超限 `budget_exhausted`；记 `budget_kind=tokens` | [x] Cost-A |
| $2 | 步数/假动作仍可能胀（残余） | M-$4, M-$6 L1 | 依赖已落地 Cap/Dec；补：低压预算 + 无进展时优先病理/预算停；X3 soft-dedup 仍归上下文维 | [ ] 持续（与 Dec/X3） |
| $3 | 缺成本归因复盘 | M-$5 | `cost_report`：steps / tokens_* / tool_counts / peak；写入 memory+transcript；Web 脚注字段对齐 | [x] Cost-B |
| $4 | 模型不知剩余预算 | M-$3 | Current State 注入 remaining steps/tokens；80% warn 短规则 | [x] Cost-B |
| $5 | 仅 steps+tokens，缺 tools/墙钟 | M-$1 | 可选 `MAX_TOOL_CALLS` / `MAX_TASK_SECONDS`；默认关或宽松 | [ ] Cost-C 可选 |
| $6 | 无费用粗估 | M-$1, M-$5 | 可选 `USD_PER_1K_TOK` × tokens → `est_usd`；无价目则跳过 | [ ] 缓做 |
| $7 | 成本维无可比 eval | M-$7 | 扩展 capability/decision 表或 `run_cost_eval`：默认预算 greeter 基线 + 极低 `MAX_TASK_TOKENS` 必现 `budget_exhausted` | [x] Cost-C |
| $8 | 多模型 cascade/routing | M-$6 L4 | 答辩叙事；本仓不做 | [ ] 缓做 |

---

### 7.6 实施计划（按此执行）

```text
阶段 Cost-A（0.5 天）— 硬闸：累计 token + 停因
  1. $1：Run 内累加 tokens_est；LLM 调用前 projected 预检
  2. 触顶 → stopped_reason=budget_exhausted（勿再打 API）
  3. config / .env：MAX_TASK_TOKENS（0=关闭，默认给作业级宽松值或关）
  4. 单测/冒烟：人为极低预算 → 早停且 reason 正确；默认预算 greeter 不误杀

阶段 Cost-B（0.5 天）— 可见 + 账单
  1. $4：Current State / SSE 暴露 remaining budget（steps+tokens）
  2. $3：cost_report 落 memory + transcript；Web FINAL 展示 tool_counts 摘要
  3. warn 阈值：剩余 ≤20% 注入一句收束提示（非第二模型）

阶段 Cost-C（与 I1 合并，0.5 天）— 证明「可控且可比」
  1. $7：eval/冒烟行：budget_stop 合成用例 + greeter tokens_total_est 基线写入 §13
  2. $5 可选：MAX_TOOL_CALLS
  3. 对比 Cost 前：同任务 tokens/steps 不显著变差；低压预算必现硬停
```

**依赖与边界：**

- 累计 token 用现有 **字符粗估** 即可（与 ContextManager 一致）；不要求对接供应商 usage 字段（有则优先累加真实 usage）。  
- `budget_exhausted` 不得破坏 tool pairing；停在「下一调用前」。  
- 默认预算宁松勿紧：误杀用 greeter live / offline 回归。  
- 与 §3 停因表统一；病理停与预算停同时触发时 **主因取更具体者**（cycle/retry > budget），次因可写入 cost_report。  
- 不引入多模型路由、不引入 BAVT 树搜索、不强制 Langfuse。  
- 成本闸若与「用户明确要求跑满」冲突 → 文档写明可 `MAX_TASK_TOKENS=0` 关闭或 CLI 覆盖。

---

### 7.7 验收标准（本章 Definition of Done）

- [x] Cost-A：`MAX_TASK_TOKENS` 极低时，run 以 `budget_exhausted` 结束且 **无** 触顶后的额外 LLM 调用。（`check_cost_a` + smoke）  
- [x] Cost-A：默认 `MAX_TASK_TOKENS=0`（关闭）下不误杀；显式低压预算必停。  
- [x] Cost-B：每轮 `cost_report` 含 steps、tokens_total_est、tool_counts；Current State 可见 `Budget:` 行；≤20% 一次性 `[budget_warn]`。  
- [x] Cost-C：`python -m scripts.run_cost_eval --offline` 五行全 Y；smoke 已挂；live 基线写入 §13。  

**本章不做完不算成本升级；仅有上下文圆环或「请节约」prompt → 仍算 Unbounded run 风险。**

#### 成本维如何测试（Cost-A/B/C）

**1）离线（无 API，必跑）**

```powershell
conda activate codeagent
cd G:\codeagent
python -m scripts.check_cost_a
python -m scripts.run_cost_eval --offline
python -m scripts.smoke_v1
```

期望：五行 `ok=Y`（unit-gate / warn-report / budget-stop-first / budget-stop-second / gate-off-no-false-kill）；smoke OK。

**2）在线 live（需 API，产出 tokens 基线）**

```powershell
python -m scripts.run_cost_eval --live
# 可选：再跑低压硬停
python -m scripts.run_cost_eval --live --low-budget 8000
```

| 列 | 含义 |
|----|------|
| `tok` | `tokens_total_est`（记入 §13） |
| `early` | 低压用例应为 Y（`budget_exhausted`） |
| `false_kill` | 基线 gate=off 应为 N |

JSON 默认：`evals/results/cost_live_*.json`。

**3）Web**  
见 Cost-B：顶栏任务 tok、`[budget_warn]`、FINAL `cost_report.summary`；低压 `MAX_TASK_TOKENS` 重启后应硬停。

### 7.8 与计划书

- 步数/窗口效率细节见计划书 **第 15 节**；病理早停见 **第 16 节**。  
- 本文 §7 为质量视角「硬闸 + 归因 + 预算可见」总纲，落地以 **§7.6** 为准。

---


## 8. 安全可控

### 8.1 已有（保持）

- 工作目录路径沙箱；shell hard-deny；`--approval auto|ask|never`  
- `risk_level` / `is_readonly`；敏感路径 Deny  
- Least Privilege 可见集；Web 上 High Deny；Completion 出口证据

### 8.2 缺口与改造方向

| ID | 缺口 | 建议动作 | 调研关键词 | 状态 |
|----|------|----------|------------|------|
| S1 | 无 OS 级沙箱 | Windows 先承认局限；可查 WSL/bubblewrap 或子进程环境剥离 | coding agent sandbox, Claude Code sandbox | [ ] 缓做 |
| S2 | 网络 / pip 无独立策略 | 升 High 或独立 allowlist；勿只靠模糊字符串 | network policy agent tools, pip install risk | [ ] |
| S3 | 子进程可读敏感文件 | 拦 `python -c`/`Get-Content` 等常见读法；文档写明残余风险 | sensitive files subprocess bypass | [ ] |
| S4 | 无链式 IFC / 注入专项 | 读不可信内容后限制写/外传；间接 prompt injection | AgenTRIM, ASI02, prompt injection agent | [ ] 缓做 |

### 8.3 与计划书

- 细节见计划书 **第 17 节**；论文：[AgenTRIM](https://arxiv.org/abs/2601.12449)、[Verifiably Safe Tool Use](https://arxiv.org/abs/2601.08012)。

### 8.4 验收

- 冒烟：读 `.env`、`git reset --hard`、正常 pytest → Deny / Deny或Confirm / Allow。  
- 新增网络/pip 策略后有对应用例。

---

## 9. 持续改进

### 9.1 已有（保持）

- transcript JSON；`scripts/smoke_v1`；计划书进度日志  
- 单次 run 内 failed strategies（TaskState）

### 9.2 缺口与改造方向

| ID | 缺口 | 建议动作 | 调研关键词 | 状态 |
|----|------|----------|------------|------|
| I1 | 无固定任务 eval 集 | 建 `evals/`：greeter 等；指标=完成率/步数/违规/是否转圈 | agent eval harness, trajectory eval | [ ] |
| I2 | 失败不回流到 Skill/阈值 | 人工复盘表：失败类型 → 改 Guard 阈值或 Skill 步骤 | agent error analysis loop | [ ] |
| I3 | 改护栏无 A/B | 同一 eval 集对比 warn/stop 阈值误伤率 | prompt/threshold ab test agent | [ ] 缓做 |

### 9.3 验收

- 一条命令跑完 eval，输出表格：任务、stopped_reason、steps、是否完成、是否违规。  
- 任何 D1/$1/V1 等改动合并前必须跑 eval 或至少冒烟扩展。

---

## 10. 优先改造清单（按这个顺序做）

> 对「严格好 Agent」增益排序。每项：调研 → 最小实现 → 冒烟/eval → 本文勾选 →（可选）计划书进度日志。

| 优先级 | 项 | 覆盖维度 | 预估 | 状态 |
|--------|----|----------|------|------|
| **P0-1** | D1 周期/停滞检测 + D3 dispatch 硬 BLOCK（§3 Dec-A） | 决策 + 成本 | 1 天 | [x] Dec-A（D2 停滞属 Dec-B） |
| **P0-2** | Capability Cap-A/B/C（已完成；live 基线用 `--live` 回填） | 能力 + 成本 + 上下文 | — | [x] |
| **P0-3** | $1 任务级 token 硬闸 + $3/$4 账单与预算可见（§7 Cost-A/B/C） | 成本 | 1～1.5 天 | [x] Cost-A/B/C |
| **P1-1** | V1 加宽 Evidence Mustlist + V2 假绿防护 | 结果可验证 | 0.5～1 天 | [ ] |
| **P1-2** | S2 网络/安装策略 + S3 子进程敏感读缓解 | 安全 | 0.5～1 天 | [ ] |
| **P1-3** | I1 固定任务 eval 集（步数/完成率/违规率） | 持续改进 | 1 天 | [ ] |

**缓做（答辩可提「下一步」）：** C4 子 Agent、D5 向量 Router、S1 OS 沙箱、S4 IFC、I3 A/B；层级规划（ReAcTree/ReCAP）仅作下一步叙事。

**Capability 专序：** 见 **§2.6**（Cap-A → Cap-B → Cap-C），与上表 P0-2 对齐。  
**Decision 专序：** 见 **§3.6**（Dec-A → Dec-B → Dec-C），与上表 P0-1 对齐。  
**Cost 专序：** 见 **§7.6**（Cost-A → Cost-B → Cost-C），与上表 P0-3 对齐。

---

## 11. 调研入口（按优先项）

| 优先项 | 建议先读 |
|--------|----------|
| **Capability（§2）** | [Claude Code 能力分类](https://code.claude.com/docs/en/how-claude-code-works)；[SWE-agent ACI](https://arxiv.org/abs/2405.15793)；[OpenHands](https://arxiv.org/abs/2407.16741)；[AgenTRIM](https://arxiv.org/abs/2601.12449)；[Harness 披露](https://arxiv.org/html/2605.23950v1)；本文 §2.1～2.2 |
| **Decision（§3）** | [OpenHands StuckDetector](https://docs.openhands.dev/sdk/guides/agent-stuck-detector)；[AWS Debounce](https://dev.to/aws/how-to-prevent-ai-agent-reasoning-loops-from-wasting-tokens-2652)；[Anatomy of Termination](https://towardsai.com/p/machine-learning/when-should-an-agent-stop-the-anatomy-of-termination)；本文 §3；**验收** `python -m scripts.run_decision_eval --offline` |
| **Cost（§7）** | 本文 §7；**验收** `python -m scripts.run_cost_eval --offline`；live：`python -m scripts.run_cost_eval --live` |
| X3 soft-dedup | Kimi tool-dedup；计划书 15.6；与 C6 大文件默认头 N 行配合 |
| V1/V2 | 计划书 17.6；[Verifiably Safe Tool Use](https://arxiv.org/abs/2601.08012) |
| S2/S3 | 计划书 17.4～17.5；[AgenTRIM](https://arxiv.org/abs/2601.12449)；Claude Code Permissions |
| I1 / C7 | Agent eval / trajectory；本仓 `demos/` + transcript；CORE/TRACE 思路简化版 |

---

## 12. 改造纪律（防踩坑）

1. **检测与拦截放在 dispatch 边界**，不要只靠 system prompt「请勿重复 / 请遵守」。  
2. **BLOCK 也要写 `role=tool` 消息**，否则 pairing 会坏。  
3. **Retry ≠ harness 自动重跑同一 handler**；应迫使模型换调用。  
4. **Stop / Complete 要可验证**；宁可用 pytest 摘要，也不要只信模型「已修好」。  
5. **病理停应早于 max_steps**；抬高步数上限不是解法。**成本闸须在下一 LLM/副作用之前同步检查**；看板变红但调用已发出 = 未实现成本可控。  
6. **安全不增加新权限面**：新工具必须带 `risk_level` / `is_readonly`，并走 `PermissionGate`。  
7. **先 eval/冒烟，再谈「感觉更好了」**。

---

## 13. 进度记录

| 日期 | 完成项 | 结论 / 指标变化 |
|------|--------|-----------------|
| 2026-08-30 | 本文档建立；基线自评完成 | 见第 1 节打分；优先序见第 10 节 |
| 2026-08-30 | **① Capability 调研 + 方法计划写入 §2** | 修正：grep/切片读已有；定案 M1–M8；实施序 Cap-A/B/C；主缺口 C1 行为、C3、C5、C7 |
| 2026-08-30 | **Capability Cap-A 落地**（C1/C5/C6） | Search-first 进 system+Skill；`.py` ast 拒写；≥100 行 auto-head；smoke_v1 OK |
| 2026-08-30 | **Capability Cap-B 落地**（C2/C3） | 新增 `run_tests` / `git_status` / `git_diff`；TaskState+Gate+可见集已接；smoke_v1 OK |
| 2026-08-30 | **Capability Cap-C 落地**（C7/C8） | `evals/` + `scripts/run_capability_eval.py`；offline 进冒烟；README 能力边界 |
| 2026-08-30 | **Live 基线** `deepseek-v4-flash`（`live_20260830_195008.json`） | **locate-string：steps=5，ok，grep+search1st=Y**；**fix-greeter：steps=6，ok，run_tests+tests_green=Y**（未用 grep：小仓直接读测+源，可接受） |
| 2026-08-30 | **② 决策合理调研 + 方法计划写入 §3** | 定案 M-D1～M-D8；对照 StuckDetector / Strands Debounce / ReflAct / Termination 四类；实施序 Dec-A/B/C；主缺口 D1 cycle、D3 硬 BLOCK、D7 eval |
| 2026-08-30 | **Decision Dec-A 落地**（D1/D3/D6） | cycle warn/stop→`cycle_detected`；exhausted fp 硬 BLOCK→`retry_exhausted`；system Decision discipline；smoke_v1 OK |
| 2026-08-30 | **Decision Dec-B 落地**（D2/D4） | 观测停滞 `STAGNATION_WARN`（默认不硬停）；debugging/testing/refactoring Skill plan-then-act；smoke + check_dec_a OK |
| 2026-08-30 | **Decision Dec-C 落地**（D7） | `evals/decision.py` + `run_decision_eval --offline`：cycle-stop early@6 / block / stag-warn / no-false-cycle；smoke 挂载；live 表加 pathology 列 |
| 2026-08-30 | **③ 成本可控调研 + 方法计划写入 §7** | 定案 M-$1～M-$7；对照 FrugalGPT / BATS / BAVT / Agent Patterns 多维闸 / Sync Gate；主缺口 $1 累计 token 硬闸、$3 归因、$4 预算可见；实施序 Cost-A/B/C；多模型级联标缓做 |
| 2026-08-30 | **Cost-A 落地**（$1） | `TaskBudget` + `MAX_TASK_TOKENS`（默认 0=关）；LLM 前同步闸 → `budget_exhausted`；memory/`task_budget` 快照；`check_cost_a` + smoke 覆盖首轮拒呼与次轮拒呼 |
| 2026-08-30 | **Cost-B 落地**（$3/$4） | Current State `Budget:` 行；≤20% 一次性 `[budget_warn]`；`cost_report`（steps/tokens/tool_counts）→ memory+transcript+Web FINAL/顶栏；SSE `task_budget`/`cost_report`/`budget_warn` |
| 2026-08-30 | **Cost-C 落地**（$7） | `evals/cost.py` + `run_cost_eval --offline` 五行；smoke 挂载；**live** `cost_live_20260830_210626.json`：fix-greeter **steps=5，tok≈26926，tools=7，completed**；`--low-budget 8000` → **budget_exhausted early@2** |
| 2026-08-30 | **Cost-C live 复跑**（`cost_live_20260830_210807.json`） | fix-greeter：**steps=5，tok≈27274，tools=7，completed**（in≈26k/out≈944）；`--low-budget 8000`：**steps=2，tok≈9561，budget_exhausted early=Y**（与首跑同量级） |
|  |  |  |

---

## 14. 一页纸口诀（答辩 / 自检）

```text
能力：五类能力地图齐不齐？Search-first 会不会做？完成有没有一等验证工具？
决策：转圈/换策能不能在 dispatch 边界硬拦？停因是否可分类且早于 max_steps？
上下文：窗口里是不是「当前任务真正需要的」？
执行：失败能不能恢复、中断能不能干净停？
验证：完成有没有证据，而不是模型自述？
成本：步数与 token 有没有硬闸与复盘？
安全：危险动作是否 Allow/Confirm/Deny 可执行？
改进：同一批任务能不能量化对比「改前/改后」？
```

八条里任意一条长期「只靠模型自觉」→ 尚未达到本文的严格 Agent 标准。
