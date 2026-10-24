# Chat LLM 逐条诉求分析工作包

- 角色 / 分支 / worktree：A integration / `codex/chat-llm-analysis` / `var/worktrees/chat-llm-analysis`。
- 可信发布基线：`98f58c5fe53c5f4048efd908c67a0e1f83bfa182`；source_base `5335d785a4fa86c98d3a1366e1875914e10496d6`；任务卡 `docs/dev/tasks/chat-llm-analysis.json`。
- 状态：ACCEPTED；实施提交 `dbc1be7` 已快进合并主线。
- 范围：新增授权 `intent-preview`，复用现有受信 LLM profile 和引文校验解释器；Chat 的提单/反馈先做逐条分析预览，失败不自动降级或创建草稿。原文仍是确认卡源数据，分析不构成批准或可执行规则。
- 凭据：私有模型配置只引用环境变量，仓库仅保留无密钥样例；当前 8084 无模型 ID/服务端密钥，真实模型联测 NOT_RUN。
- 测试：`PYTHONPATH=src:tests python3 -m unittest tests.test_chat_intent tests.test_chat_ui tests.test_model_runtime -q`，18 项 PASS；`PYTHONPATH=src python3 -c 'from dsh_retrieval.chat_ui import CHAT_JS; print(CHAT_JS)' | node --check` PASS；`git diff --check` PASS。
- 完整工作包门禁：`python3 tools/collab_gate.py package --base 98f58c5fe53c5f4048efd908c67a0e1f83bfa182 --task docs/dev/tasks/chat-llm-analysis.json`，628 项 PASS，`LOCAL GATE PASS`，退出码 0；按高风险包要求运行一次，未重复回归。
- 未验证：当前 8084 模型目录为空，真实上游模型 ID 和私有密钥尚未配置；真实请求/响应、延迟及质量联测 NOT_RUN。模型输出只作为逐条审阅卡，不保证自动生成可执行规则或持续多轮上下文记忆。
- 下一步：收到模型 ID 并经私有环境变量提供密钥后，在隔离端口启动配置服务，做真实 LLM 诉求及反馈冒烟；再决定是否扩展多轮上下文分析。
