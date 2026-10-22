# A 总控门禁补齐任务

- 授权：用户明确要求“补齐”；仅实现严格门禁，不通过放宽规则使历史失败变成通过。
- 角色 A；独立 worktree `/Users/hw-edit/.codex/worktrees/integration-gate/search-agent`；分支 `codex/integration-gate`；基线 `22c62143dd20d49f0426f1fccd2232fd13de6699`。
- 范围：tools/collab_gate.py、tests/test_collab_gate.py、docs/dev/ownership.json、docs/dev/parallel-plan.md、docs/dev/window-prompts.md、本任务卡、docs/dev/tasks/intent-model.json、docs/dev/handoffs/integration.md。不得修改其他 worktree 的模型接入代码。
- 依据 v1.9 16.4、17.2、17.6–17.7：门禁检查开发切片与证据，不将本地测试声称为业务全量验收。
- 验收：新增 integration 角色，只从可信基线读取已发布 JSON 任务卡与责任表，要求独立 worktree/指定分支/有效基线/精确文件清单/交接；拒绝 B/C/D 所属文件、任务卡自扩权、策略自扩权、符号链接、路径穿越及新增未授权文件；范围通过后仍执行原完整回归。原三个角色定义保持不变。
- 任务卡绑定角色、分支、起始提交、验收说明及精确文件清单；起始提交必须为发布基线祖先。CLI 的 --base 是发布任务卡的可信基线，不在文件内填自引用提交 SHA。
- 引导限制：旧基线没有 integration 配置，旧门禁不能认证新增门禁本身。以隔离 Git 测试验证新规则，保留旧门禁失败证据；需人工审查并授权发布可信基线后，模型工作区才能同步并运行新门禁。不得以候选分支改写的任务卡临时放行。
