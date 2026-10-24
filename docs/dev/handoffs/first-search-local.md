# 本地首次检索 1–5 工作包

- 角色 / 分支 / worktree：A integration / `codex/first-search-local` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/first-search-local`。
- 可信发布基线：`f18caeeb760d08dedc9e4ad5ee5d9b06dd894e39`；source_base `05d900ddf39f97f811ea217ae098484925a96733`；任务卡 `docs/dev/tasks/first-search-local.json`。
- 状态：ACCEPTED；定向、HTTP/UI、完整工作包门禁及本地页面冒烟均已通过；实施提交 `327ec3b` 已快进合并主线。
- 目标：v1.9 6.5–6.7、13.6、A37/A44/A45 的本地子集；从显式选择索引到整组批准、逐批检索、授权候选展示。未接真实 Milvus、模型、元数据或媒体字节。
- 已实施：固定 LOCAL_EXACT 双索引夹具与服务端范围模板；夹具内容摘要绑定检索请求以阻断换库静默重查；单次手动 tick 调用现有协调器及 SQLite 账本；授权读取已提交页并解析映射状态；Chat 显示确认与执行步骤，原始候选不冒充清洗合格素材。
- 本地约束：服务需显式 `--local-first-search-fixture`，独立 DSH_HOME/SQLite/loopback；密钥只来自环境变量；默认不开放。UI 不展示媒体引用或缩略图，不自动清洗、补数、导出。
- 定向验证：本地服务/API/UI/账本相关测试 25 项 PASS；JavaScript `node --check` PASS，`git diff --check` PASS。
- 完整工作包门禁：`python3 tools/collab_gate.py package --base f18caeeb760d08dedc9e4ad5ee5d9b06dd894e39 --task docs/dev/tasks/first-search-local.json`，624 项 PASS，`LOCAL GATE PASS`，退出码 0。按任务卡高风险要求仅运行这一次完整回归。
- 页面冒烟：独立 8084 服务、独立 `DSH_HOME` 与 SQLite 账本。用“找动物图片”提单后，显式选择 cats/cat-v1 与 dogs/dog-v1，确认整组、手动执行两批；首批两库各 2 条，第二批仅 dogs 追加 1 条 `mapping_missing`；共 5 条原始向量命中，界面明确有效素材数未知，且无媒体私有引用或缩略图。PASS。
- 未验证：真实 Milvus、真实 embedding、权威元数据/媒体服务、实际图像预览、生产并发/租约/费用均 NOT_RUN；此结果不代表真实内网端到端验收。
- 下一步：真实网关落地需受信索引/模型/授权/映射接口资料及独立联测工作包。
