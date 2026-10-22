# input-only 精确去重预览发布账本任务卡

- 角色 core；独立 worktree `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/core`；分支 `codex/task-core`；契约基线 `c0d6bf7326d1a317005c9ae223217a0d5b4f7730`，执行基线为包含本任务卡的主分支提交。
- 仅修改 `src/dsh_retrieval/core/`、`src/dsh_retrieval/adapters/task_storage/`、`tests/core/` 和 `docs/dev/handoffs/core.md`。消费 `ExactResultSetPreview`，不修改公共契约、cleaning/retrieval 私有目录、门禁或 DJ。
- 在现有多规则 SQLite 账本中，对已持久的完整清洗结果构建精确去重 preview，并以短事务重验创建人、项目/素材授权、task/revision/generation/task_version/operation、确认规则及 input_only 策略后，原子写入不可变 result_set、当前指针、统计事件和任务版本/阶段。
- 验收：目标内 accepted_count、reserve、fail/unknown/重复、gap 和停止原因可重建；同源结果幂等、多连接并发仅一次发布；旧代次/版本、撤权、策略不匹配、结果篡改拒绝；序列化失败全回滚；重启后可读且读取再授权。恒为 preview/不派发补数，不宣称 delivery 或真实全库穷尽。完整回归及 core 角色门禁通过。
