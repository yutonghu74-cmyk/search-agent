# 多规则清洗本地批准账本任务卡

- 角色 core；独立 worktree `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/core`；分支 `codex/task-core`。契约源基线 `8c6b9c9248e64cba0e6fc6e701f73f396bdc9a8d`；执行基线为包含本任务卡的主分支提交。
- 只改 `src/dsh_retrieval/core/`、`src/dsh_retrieval/adapters/task_storage/`、`tests/core/`、`docs/dev/handoffs/core.md`。消费 `CleaningApprovalProposal`/`CleaningApproval` 与 `require_confirmed_rules`，不改共享契约、检查器或 DJ。
- 实现 SQLite 本地账本：从当前受信任务、已发布且仍授权的候选清单、匹配正式 QuerySpec 的配方生成提案；显式发起人确认摘要/期望版本/幂等键后，在同一事务更新任务版本与作业记录/待发事件。重试最多一次生效，同键异载荷冲突；撤权/旧版本/旧 generation/输入变化失败关闭。
- 验收：多连接并发一次生效、重启读回当前批准作业、旧代次不可读取、非发起人和权限故障拒绝、不创建有效数量或调用检测器。只做本地 SQLite 开发切片，非生产账本或真实 DJ 多规则联测。
- 交付前运行 `python3 tools/collab_gate.py check --role core --base <本任务卡发布提交>`，交接记录真实结果后由 A 审查、本地集成。
