# 候选清单本地持久化与确认

- A / codex/integration / var/worktrees/integration；基线 83b3607a96603e7e6ba18adbd62e1d57f24ed9f8。范围：candidate_contracts.py、candidate_publication.py、ports.py、tests/test_candidate_contracts.py、本任务卡、integration 交接。先发布共享契约，再由 core 同步实施。
- core / codex/task-core / var/worktrees/core；以 A 发布的完整 SHA 为基线，登记在 core 交接。仅 task_storage/、tests/core/ 和 core 交接；不得修改检查器、责任表、共享模型。
- 规格依据：13.2—13.5、14.6；A12/A20/A36/A45/A50/A51 的本地子集。原始候选清单不是合格 result_set，不改变 accepted_count，不创建 cleaning operation。
- 持久内容是可信媒体引用、版本、SHA-256、原始命中及批准计划/作业血缘；不保存临时路径、媒体字节或重复的查询向量。不提供任意客户端清单导入。
- 发布流程：服务物化并校验 → 存提案 → 显示摘要和 expected_task_version → 发起人显式确认 → 重新物化校验摘要 → 短事务核对当前任务/计划/已提交页、媒体权限 → 清单/幂等键/本地待发事件原子写入。下载不在数据库事务中。
- 首次确认严格检查 task_version；同提案多键并发只有一个清单和事件，同键异体冲突。确认仅发布输入工件，不推进任务状态或增加版本，避免冻结后续检索页；清洗启动仍独立确认并由既有服务递增版本。
- 已发布清单读取允许同 revision/generation/需求/当前计划下 task_version 单调增加；不能因此重新执行旧检索。暂停、取消、归档、FINALIZING、旧 generation/计划或撤权拒绝。该例外只适用于清单读取，不修改 RetrievalPlanRepository 的严格版本栅栏。
- 空页可保存/确认空清单但不得启动清洗；单页上限 100 命中。单页清单不是全任务候选池、跨页增量账本或正式结果集。
- 验收：身份/任务隔离、来源覆盖与不误合并、摘要稳定、不泄漏路径、重启、并发确认、幂等冲突、权限失败回滚、旧版本拒绝、读不写，以及清洗版本递增后清单仍能授权读取。完整模拟集成回归，不运行真实 DJ/DSH、内网、不安装依赖或推送。
- 局限：持久化清单不保证来源永久可下载；使用时必须重新鉴权和验摘要。媒体访问与本地事务非原子；未实现强杀临时文件回收、生产存储、事件 relay 或清洗装配。
