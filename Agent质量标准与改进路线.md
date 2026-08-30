# Agent 质量标准与改进路线

> **用途：** 交付后 / 有余力时，按本文档的维度与优先级做调研与改造。  
> **判定标准：** 好的 Agent = 能力完整 + 决策合理 + 上下文有效 + 执行可靠 + 结果可验证 + 成本可控 + 安全可控 + 能持续改进。  
> **基线日期：** 2026-08-30  
> **对照代码：** `src/agent/`、`src/tools/`、`src/web/`  
> **与计划书关系：** 本文是「质量视角」总纲；细节实现仍可回链计划书第 11～17 节。改完一项后在本文勾选，并视需要回写计划书进度日志。

**当前总判：** 作业级 / 答辩级已达标；离「严格好 Agent」还差一层——主要在决策机械性、检索能力、成本硬闸、安全深度、离线评测闭环。

---

## 0. 怎么用这份文档

1. **先交付再大改**：Day4（视频 / zip）未完成前，不要开大坑。  
2. **按第 9 节六项优先序推进**：每项先调研 0.5～1 天，再最小落地。  
3. **每改一项必须有验收**：冒烟 +（更好）固定任务 eval 指标。  
4. **勾选约定**：`- [ ]` → `- [x]`；在「进度」小节补一行日期与结论。

---

## 1. 总览打分（基线）

| 维度 | 基线 | 一句话 |
|------|------|--------|
| 能力完整 | B+ | 读写跑测 + Todo/Skill/记忆够用；缺 grep、子 Agent、强检索 |
| 决策合理 | B | Guard/Retry/可见集已做；周期转圈、策略换法仍偏软 |
| 上下文有效 | A- | 压缩/预算/Current State 扎实；长会话语义记忆仍弱 |
| 执行可靠 | B+ | 错误回传、配对修复、取消/steer 有；Web 审批 UX 不全 |
| 结果可验证 | A- | Evidence Gate + 测试解析是亮点；证据种类偏窄 |
| 成本可控 | B- | 有预算条与批工具；无「步数×token」硬预算与 ROI 停 |
| 安全可控 | B+ | 沙箱/三级风险/敏感路径到位；无 OS 沙箱、链式 IFC、网络策略 |
| 持续改进 | C+ | transcript + 冒烟有；缺轨迹评测、失败归因、自动改 Skill |

**目标（交付后 1～2 周可冲）：** 决策 / 成本 / 持续改进各至少升半档；六项优先改造全部勾完。

---

## 2. 能力完整

### 2.1 已有（保持）

- 工具：`read_file` / `write_file` / `edit_file` / `list_dir` / `glob` / `run_shell` / `todo_write`（及 Skill / memory / rag 相关）
- 入口：CLI + Web；同一套 `loop.py`
- Skill：`debugging` / `testing` / `refactoring`（轻量渐进披露）
- 特色：Plan-then-Act（todo）+ 可观测 timeline

### 2.2 缺口与改造方向

| ID | 缺口 | 建议动作 | 调研关键词 | 状态 |
|----|------|----------|------------|------|
| C1 | 缺内容检索（grep / 符号搜索） | 新增 `grep` 或等价工具；大文件优先搜再切片读 | codebase search, ripgrep tool, Claude Code Grep | [ ] |
| C2 | 无结构化 git / diff 工具 | 可选：`git_status` / `git_diff` 专用工具，减少裸 shell | structured git tools agent | [ ] |
| C3 | 测试只能靠 shell 启发式 | 可选：`run_tests` 包装 pytest，统一解析 exit / 摘要 | test runner tool agent | [ ] |
| C4 | 不做子 Agent（已知取舍） | 复杂任务可调研「有界委托 / 隔离上下文」；非必须 | subagent bounded context, mini-claude-code v3 | [ ] 缓做 |

### 2.3 验收

- 陌生小仓定位某函数/报错字符串，步数明显少于「只 list+read」。  
- 冒烟覆盖新工具 schema + 沙箱路径。

---

## 3. 决策合理

### 3.1 已有（保持）

- `LoopGuard`：同轮 dedup、连续 exact ≥3 warn / ≥5 stop  
- error-streak nudge；`RetryPolicy` 阶梯 → `retry_exhausted`  
- `TaskState` + Current State 注入  
- `tool_visibility` 按阶段收窄可见工具  
- Skill 关键词预注入

### 3.2 缺口与改造方向

| ID | 缺口 | 建议动作 | 调研关键词 | 状态 |
|----|------|----------|------------|------|
| D1 | A↔B 周期 / ping-pong 未拦 | 滑动窗口 cycle 检测（长度 2～4）；warn→stop | OpenHands StuckDetector, tool-loop-guard cycle | [ ] |
| D2 | 输出停滞 / 模糊相似 args | 可选：观测指纹不变计数；fuzzy args | output stagnation, fuzzy tool dedup | [ ] |
| D3 | Retry 靠自然语言，可被无视 | 在 dispatch 边界对黑名单 fingerprint **硬 BLOCK** | strategy blacklist dispatch, AWS Strands debounce | [ ] |
| D4 | 批工具 / 粗 todo 仅 prompt 软约束 | 评估是否做 schema/策略级强制（慎防误伤） | parallel tool calls force, plan then act | [ ] |
| D5 | 任务路由偏关键词 | Skill 增多后再上轻量 router；勿过早向量双塔 | SkillRouter, progressive disclosure | [ ] 缓做 |

### 3.3 与计划书

- 细节见计划书 **第 16 节**（P2：cycle / soft-dedup）。

### 3.4 验收

- 构造 `read↔pytest` 交替轨迹，应 warn 并早于 `max_steps` 停。  
- 同 failure_key 第三次应 `retry_exhausted`，且第四次同 fingerprint 不执行。

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

## 7. 成本可控

### 7.1 已有（保持）

- 上下文预算与剩余条；输出截断；批工具 prompt；可见集减 schema  
- `max_steps` 资源终止

### 7.2 缺口与改造方向

| ID | 缺口 | 建议动作 | 调研关键词 | 状态 |
|----|------|----------|------------|------|
| $1 | 无任务级 token/费用硬上限 | `MAX_TASK_TOKENS` / 费用闸；超限 `budget_exhausted` | agent token budget, resource termination | [ ] |
| $2 | 步数膨胀仍靠自觉 | 落地 C1 切片读 + X3 soft-dedup；粗 todo 保持 | step efficiency coding agent | [ ] |
| $3 | 缺成本归因复盘 | transcript/面板按「步 / 工具 / 压缩前后」汇总 | LLM cost observability | [ ] |

### 7.3 与计划书

- 细节见计划书 **第 15 节**（步数 / Token 效率）。

### 7.4 验收

- 同一 greeter 任务：平均步数、总估算 token 有基线数字，改造后可对比下降。  
- 人为压低 token 预算时，有明确 `stopped_reason`，而非静默截断致胡言。

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
| **P0-1** | D1 周期/停滞检测 + D3 dispatch 硬 BLOCK 换策 | 决策 + 成本 | 1 天 | [ ] |
| **P0-2** | C1 `grep`/切片读 + X3 soft-dedup | 能力 + 成本 + 上下文 | 1 天 | [ ] |
| **P0-3** | $1 任务级 token/步数双预算 + $3 成本复盘 | 成本 | 0.5～1 天 | [ ] |
| **P1-1** | V1 加宽 Evidence Mustlist + V2 假绿防护 | 结果可验证 | 0.5～1 天 | [ ] |
| **P1-2** | S2 网络/安装策略 + S3 子进程敏感读缓解 | 安全 | 0.5～1 天 | [ ] |
| **P1-3** | I1 固定任务 eval 集（步数/完成率/违规率） | 持续改进 | 1 天 | [ ] |

**缓做（答辩可提「下一步」）：** C4 子 Agent、D5 向量 Router、S1 OS 沙箱、S4 IFC、I3 A/B。

---

## 11. 调研入口（按优先项）

| 优先项 | 建议先读 |
|--------|----------|
| D1/D3 | [OpenHands StuckDetector](https://docs.openhands.dev/sdk/guides/agent-stuck-detector)；计划书 16.2 表；Towards AI *Anatomy of Termination* |
| C1/X3 | CoreCoder / Claude Code 工具集；Kimi tool-dedup；计划书 15.6 |
| $1/$3 | 计划书第 15 节；Resource Termination 分类 |
| V1/V2 | 计划书 17.6；[Verifiably Safe Tool Use](https://arxiv.org/abs/2601.08012) |
| S2/S3 | 计划书 17.4～17.5；[AgenTRIM](https://arxiv.org/abs/2601.12449)；Claude Code Permissions |
| I1 | Agent eval / trajectory benchmark 实践文；本仓 `demos/` + transcript 复放 |

---

## 12. 改造纪律（防踩坑）

1. **检测与拦截放在 dispatch 边界**，不要只靠 system prompt「请勿重复 / 请遵守」。  
2. **BLOCK 也要写 `role=tool` 消息**，否则 pairing 会坏。  
3. **Retry ≠ harness 自动重跑同一 handler**；应迫使模型换调用。  
4. **Stop / Complete 要可验证**；宁可用 pytest 摘要，也不要只信模型「已修好」。  
5. **病理停应早于 max_steps**；抬高步数上限不是解法。  
6. **安全不增加新权限面**：新工具必须带 `risk_level` / `is_readonly`，并走 `PermissionGate`。  
7. **先 eval/冒烟，再谈「感觉更好了」**。

---

## 13. 进度记录

| 日期 | 完成项 | 结论 / 指标变化 |
|------|--------|-----------------|
| 2026-08-30 | 本文档建立；基线自评完成 | 见第 1 节打分；优先序见第 10 节 |
|  |  |  |

---

## 14. 一页纸口诀（答辩 / 自检）

```text
能力：工具够不够完成这类编程任务？
决策：转圈能不能在边界上被拦住并换策？
上下文：窗口里是不是「当前任务真正需要的」？
执行：失败能不能恢复、中断能不能干净停？
验证：完成有没有证据，而不是模型自述？
成本：步数与 token 有没有硬闸与复盘？
安全：危险动作是否 Allow/Confirm/Deny 可执行？
改进：同一批任务能不能量化对比「改前/改后」？
```

八条里任意一条长期「只靠模型自觉」→ 尚未达到本文的严格 Agent 标准。
