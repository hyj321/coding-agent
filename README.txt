编程智能体（Code Agent）提交说明
================================

一、仓库地址
https://github.com/hyj321/coding-agent
（题目发布后新建的公开仓库，含完整提交历史。）

二、项目简介
自研 coding agent harness：通过 DeepSeek（OpenAI 兼容）原生 tool calling，
在本地读写文件、执行命令，完成编程任务。未使用 LangChain / AutoGen 等
agent 框架；主循环、工具注册与执行、上下文管理、权限与 transcript 均为自研。

三、环境与运行
1. Python 3.11+，建议 conda：
   conda create -n codeagent python=3.11 -y
   conda activate codeagent
   cd <仓库根目录>
   pip install -r requirements.txt
2. 复制 .env.example 为 .env，填写 DEEPSEEK_API_KEY（勿提交密钥）。
3. 离线冒烟：python -m scripts.smoke_v1
4. 演示任务（推荐录视频）：
   python -m src.main -w demos --approval auto --max-steps 20 "阅读 greeter_test.py，修复 greeter.py 使测试全部通过。请先用 todo_write 列出计划，再逐步执行并更新 todo 状态，最后运行 python greeter_test.py 验证。"
5. 可选 Web UI：python -m src.web ，浏览器打开 http://127.0.0.1:7860

四、特色功能
Plan-then-Act（todo_write）：非平凡任务先写检查清单，保持至多一项
in_progress，边做边更新，降低跑偏；过程在日志与 Web Plan 面板可见。

五、核心能力（答辩可讲）
- Agent 主循环：调用模型 → 鉴权 → 执行工具 → 结果回写 messages → 直至无
  tool_calls / 达 max_steps / 用户中断。
- 工具：read/write/edit、list_dir、glob、run_shell、todo_write，以及
  memory_search / rag_search（关键词与本地 TF–IDF 召回）。
- 安全：路径限制在 workdir；危险命令 hard-deny；--approval auto|ask|never。
- 上下文：工具观测压缩、Context Manager 分层与超预算折叠、MicroCompact；
  项目级 MEMORY.md 与 working_memory.json 双轨记忆。
- 可观测：transcripts 落盘；Web 步骤卡片、Changed files、session 续写。

六、设计要点
模型只输出意图，副作用在本地；工具结果必须进入对话历史；新工具只需注册
schema+handler，主循环不必改。详细说明见仓库 README.md 与 demos/DEMO.md。

七、视频说明
视频中演示上述 greeter 修复任务，可见 todo 规划、读改文件、跑测试通过，
并简要说明主循环与 Todo 特色（≤2 分钟）。
