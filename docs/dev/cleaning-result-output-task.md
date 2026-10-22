# 通用清洗执行器共享结果输出任务卡

- 角色 cleaning；独立 worktree `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/cleaning`；分支 `codex/cleaning-adapter`；契约源基线 `500d922df368c815f104c20ad7ef0c6557eeee56`，执行基线为包含本任务卡的主分支提交。
- 仅修改 `src/dsh_retrieval/adapters/cleaning/`、`tests/cleaning/` 和 `docs/dev/handoffs/cleaning.md`。消费已发布的 `CleaningJobResult` 系列契约，不修改公共模型、账本、门禁或 DJ。
- 将 `GenericCleaningExecutor.run` 输出迁移为共享契约，保持现有三态、原因、证据和完整结果语义；返回前调用完整性校验。不持久化、不计数、不触发补数。
- 验收：现有 cleaning 测试语义不回退；输出类型为共享 `CleaningJobResult`；候选/规则顺序、pass/fail/unknown、证据引用与绑定保持不变；完整回归及 cleaning 角色门禁通过。
