0. Git 仓库地址

GitHub：https://github.com/hyj321/coding-agent

HTTPS 克隆：
  git clone https://github.com/hyj321/coding-agent.git


SSH 克隆：
  git clone git@github.com:hyj321/coding-agent.git
  cd coding-agent


1. 项目简介

Code Agent 是自研 Coding Agent Harness：基于 DeepSeek（OpenAI 兼容）tool calling，
在本地工作区内读写文件、执行命令、运行测试，完成编程任务。

循环、工具注册、上下文管理、权限控制与 transcript 均由本项目自行实现，不依赖
LangChain、AutoGen 等 Agent 框架。设计原则为：模型仅输出意图，副作用在本地 Harness
执行；安全校验与完成判定由 Harness 负责，不依赖模型自觉。


2. 如何运行

2.1 环境准备

  要求：Python 3.11+，可用的 DeepSeek API Key。

  conda create -n codeagent python=3.11 -y
  conda activate codeagent
  pip install -r requirements.txt
  copy .env.example .env    # Linux/macOS: cp .env.example .env
  # 编辑 .env，填入 DEEPSEEK_API_KEY

2.2 CLI 快速体验

  conda activate codeagent
  python -m scripts.smoke_v1    # 可选冒烟

  python -m src.main -w demos --approval auto ^
    "阅读 greeter_test.py，修复 greeter.py 使测试通过，最后运行测试。"


2.3 Web 界面

  conda activate codeagent
  python -m src.web

  浏览器访问 http://127.0.0.1:7860。提交任务后，时间线展示各步工具调用；
  右侧提供 Plan 与 Files 面板；底部可查看 Changed files 对比；高风险操作
  弹出审批条；护栏事件以 EVIDENCE、FAKE_GREEN、DENY 等气泡呈现。


3. 特色功能说明

3.1 Agent 核心能力

  采用 Plan-then-Act：模型通过 todo_write 维护粗粒度阶段计划，可与读文件等同轮
  批量执行。工具面覆盖读写改、grep/glob、run_tests、shell、只读 git、memory 与
  rag 搜索等。Skills 采用渐进披露，内置 debugging、testing、refactoring 等能力。

3.2 安全与权限

  操作限定于指定工作区路径沙箱内。工具按 risk_level 分级，敏感路径硬拒绝；
  Web 模式下高风险工具需用户审批后方可执行。须说明：本方案为应用层约束，非
  OS 级沙箱。

3.4 记忆与续写

  任务过程中写入 working_memory.json，记录阶段计划与工具轨迹；任务结束后可
  追加 MEMORY.md 文本总结；每轮写入 episodes.jsonl 结构化结案卡。新会话启动时
  按目标召回相关记录，支持跨 run 回忆任务内容与代码变更。

3.5 风格卡

  Web 界面支持将工作区文件与 Styles 风格卡拖入输入栏一并提交。风格卡可定义
  文案或代码风格，Agent 据此改写指定文件，并可随后运行验证以确认行为正确。


4. 创新点说明

4.2 创新点一：完成靠证据

  模型在无 tool_calls 时声称任务完成，Harness 不直接结束，而检查 Mustlist：
  测试须语义通过；须运行与任务相关的用例；若修改过代码，须至少改动业务源文件，
  拦截仅改测试的 Fake Green。证据不足则注入 nudge 继续循环。Done 由 Harness
  状态判定，而非模型语气。

4.3 创新点二：按阶段收窄工具面

  每步调用 LLM 前，据 in-progress todo 或 goal 推断阶段（explore / edit /
  verify / full），动态收窄本步 tool schema：探索期只读，编辑期全工具，验证期
  限制 write_file。可见性管「是否可见」，PermissionGate 管「是否允许执行」，
  二者分工明确。

4.4 创新点三：分层上下文

  长任务不采用从前往后的简单截断，而采用五层上下文（System / Task / Current
  State / Recent / Historical），配合观测压缩、同文件 soft-dedup、MicroCompact
  与 Fold，并回注 MEMORY、focus 与未完成 todo。跨 run 依赖 MEMORY.md、
  working_memory.json、Episode 与本地 TF-IDF RAG 预取，在 token 预算内保留
  任务状态。
