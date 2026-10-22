# 通用清洗执行契约切片

- A / `codex/integration` / 独立 worktree `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；可信基线 `5e1c1c0`，任务卡 `docs/dev/tasks/generic-cleaning-contract.json`。
- 修改范围：`src/dsh_retrieval/multi_rule_contracts.py`、`tests/test_multi_rule_contracts.py`、`contracts/multi-rule-cleaning.md`、本任务卡、`docs/dev/handoffs/integration.md`。
- 依据 v1.9 7.3、7.7、7.8、16.2 A01/A09/A14：冻结已批准任务/版本/代次/输入/配方绑定；检测事实与业务规则分离；缺证据不得通过。当前是内部契约，不开放用户提交运行作业的 API。
- 需求解释草稿和模型输出不得直接构造正式执行权限。业务服务确认任务/方案后，才可创建 ApprovedCleaningJob。媒体入口由后续实现的受信读取接口注入；无用户路径/脚本/下载地址。
- 验收：非法版本/摘要/引用/重复规则/任意模块路径失败；合成多规则契约测试；完整回归与 integration 角色门禁。此切片不运行真实 DJ、不发布检测模型、不声明全规格验收。
