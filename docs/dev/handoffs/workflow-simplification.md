# 工作包：协作流程简化

- 授权：2026-10-24 用户明确要求“简化流程”，承接此前保留业务安全、简化角色流程的评估。此为一次独立治理升级，不是为 R06 的旧门禁失败放行。
- 主责：A / integration；分支 codex/workflow-simplification。
- worktree：/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/workflow-simplification。
- 基线：90793f88c21b2ca126bc50a8eed38ba7ef5261a0。
- 范围：AGENTS.md、docs/dev/parallel-plan.md、docs/dev/window-prompts.md、docs/dev/handoff-template.md、tools/collab_gate.py、tests/test_package_gate.py、本交接及 docs/dev/progress.md。不改 ownership、旧防绕过测试、业务代码、数据库或其他窗口草稿。
- 验收：旧命令与安全拒绝行为不变；新包按可信卡指定分支/精确跨模块范围/独立交接；定向失败和空集非零；高风险禁止降级；完整测试通过。v1.9 第 16、17 节业务验收边界保持不变。
- 状态：READY_FOR_REVIEW，治理变更与验证完成；不是旧角色范围门禁认证。

## 实现

- 新增 package 命令；从可信基线读取卡，不从候选修改读取授权。旧 check 命令固定分支及全量回归保留兼容。
- 角色标签不限制新包跨模块授权；普通包仍不得修改治理文件、任务卡或防绕过测试。
- full/targeted/docs 策略随卡冻结，路径规则保守要求高风险完整回归；总控仍须评估文件名无法识别的语义风险。
- 文档更新为一次分派、同包接口/实现、独立 handoff、阶段性全量、里程碑进度。不迁移或中断其他任务。

## 验证记录

- 初轮定向：PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_collab_gate tests.test_package_gate -q；退出 0，78 项 PASS。该轮因测试类导入重复发现旧测试，已改为模块导入消除重复；以随后完整回归数量为准。输出中的预期 FAIL/ERROR 是隔离夹具用于验证子进程非零，外层测试断言通过。
- git diff --check：退出 0。
- 完整回归：PYTHONDONTWRITEBYTECODE=1 python3 tools/collab_gate.py baseline，退出 0，573 项 PASS（30.390 秒，含旧门禁测试和新增 18 项工作包测试）。此命令仅用于治理升级的完整测试，不称为旧角色范围门禁通过；本次修改的合法性来自用户独立治理授权，不能自认证规则已获外部审查。临时日志 /tmp/search-agent-workflow-simplification-tests.log。
- 未验证：真实检索/模型/业务环境、服务重启、远端 CI 强制门禁，均与本治理包无关且 NOT_RUN。

## 下一步

交付治理升级。已有旧任务不自动改卡或放行；后续由总控在新可信基线按新模式分配完整 R06 包，保留其当前草稿。主工作区现有未跟踪 frontend-ui-optimization.json 不属于本包，不暂存、不修改。
