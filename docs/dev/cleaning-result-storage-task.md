# 多规则清洗结果本地发布账本任务卡

- 角色 core；独立 worktree `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/core`；分支 `codex/task-core`；契约源基线 `500d922df368c815f104c20ad7ef0c6557eeee56`，cleaning 输出集成基线 `e781a8c990668576941a20a3cb1eb4758b5a835d`，执行基线为包含本任务卡的主分支提交。
- 只改 `src/dsh_retrieval/core/`、`src/dsh_retrieval/adapters/task_storage/`、`tests/core/` 和 `docs/dev/handoffs/core.md`。消费共享 `CleaningJobResult` 与完整性校验，不修改契约、cleaning/retrieval 私有目录、门禁或 DJ。
- 在已有 SQLite 多规则批准账本中原子发布完整逐素材结果与待发事件。每次提交重新检查创建人、项目/素材授权、task/revision/generation/task_version/operation、确认规则、候选清单和配方。同结果重放返回已发布，同 operation 不同结果冲突；旧代次、撤权、不完整或伪造输出不得落账。
- 验收：进程重启可读；多连接并发仅一次发布效果；序列化失败时结果/事件全回滚；旧 generation、任务版本变更、撤权、绑定/候选/规则不匹配均拒绝；发布不修改 task_version、不计算 accepted_count、不派发补数。完整回归及 core 角色门禁通过。
