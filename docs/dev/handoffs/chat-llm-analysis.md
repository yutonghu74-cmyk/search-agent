# Chat LLM 逐条诉求分析工作包

- 角色 / 分支 / worktree：A integration / `codex/chat-llm-analysis` / `var/worktrees/chat-llm-analysis`。
- 可信发布基线：`98f58c5fe53c5f4048efd908c67a0e1f83bfa182`；source_base `5335d785a4fa86c98d3a1366e1875914e10496d6`；任务卡 `docs/dev/tasks/chat-llm-analysis.json`。
- 状态：ACCEPTED；实施提交 `dbc1be7` 已快进合并主线。
- 范围：新增授权 `intent-preview`，复用现有受信 LLM profile 和引文校验解释器；Chat 的提单/反馈先做逐条分析预览，失败不自动降级或创建草稿。原文仍是确认卡源数据，分析不构成批准或可执行规则。
- 凭据：私有模型配置只引用环境变量，仓库仅保留无密钥样例；8084 仍无模型配置。2026-10-24 另在仓库外 `/Users/hw-edit/.config/dsh-private/` 建立 700 私有目录、600 模型配置/密钥/游标密钥文件；不记录密钥值。
- 测试：`PYTHONPATH=src:tests python3 -m unittest tests.test_chat_intent tests.test_chat_ui tests.test_model_runtime -q`，18 项 PASS；`PYTHONPATH=src python3 -c 'from dsh_retrieval.chat_ui import CHAT_JS; print(CHAT_JS)' | node --check` PASS；`git diff --check` PASS。
- 完整工作包门禁：`python3 tools/collab_gate.py package --base 98f58c5fe53c5f4048efd908c67a0e1f83bfa182 --task docs/dev/tasks/chat-llm-analysis.json`，628 项 PASS，`LOCAL GATE PASS`，退出码 0；按高风险包要求运行一次，未重复回归。
- 后续真实配置与联测：用户提供的 HTTPS 接口列出 `gpt-4o-mini`；独立 8085 配置 `chat-llm` 和 `cleaning-vlm` profile，模型目录 HTTP 返回两项。合成诉求“找3张狗图片，不要文字”真实调用两次成功返回 `LIVE_MODEL_INTERPRETATION`，浏览器显示分析审阅卡和待确认提单卡；未代用户确认。首次调用因模型将 `occurrence` 当字符位置被拒绝，独立提示修正包 `intent-occurrence-prompt` 经 628 项门禁后合并；保留严格引文校验。8085 已重启使用持久私有游标签名密钥，页面和模型目录 HTTP 200；8084 未重启且旧任务账本未改。
- 未验证：反馈真实模型调用、模型长期稳定性与语义质量、生产授权、正式清洗管线仍 NOT_RUN。当前模型把合成输入中的标点视作未解析，能力状态为 unsupported，`execution_allowed=false`；模型分析不自动生成可执行规则或持续多轮上下文记忆。
- 下一步：使用不同诉求与反馈做质量评测，修正仅标点未解析及主体/数量拆分；再决定是否扩展多轮上下文分析。
