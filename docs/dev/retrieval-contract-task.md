# A：检索批准与回传契约切片

- 角色 A；分支 codex/integration；独立 worktree var/worktrees/integration。
- 基线：b2cdedd7b966ad7e4756c127525f81dafd9400fc。
- 授权更新（2026-10-22）：用户确认继续本地提交及审查后合并；不推送、不部署、不访问内网。A 发布本契约基线后，retrieval 同步该基线再做适配兼容验证。
- 范围：新增公共 retrieval_contracts.py、ports.py 中检索账本协议、contracts/retrieval-plan.md、tests/test_retrieval_contracts.py、本任务卡和 integration 交接。
- 验收：v1.9 6.5–6.7、11.1.1、11.2.2、13.5、14.1.1、16.2 A12/A14/A16/A20/A37/A49 的契约级子集。覆盖发起人/任务/版本/代次/epoch、显式确认摘要、旧回传拒绝、空页与异常分页区别。
- 不实现正式确认存储/事务、HTTP、真实召回、自动派发、候选发布、清洗装配和生产持久化。纯校验器不表示操作已经批准或提交。
- 验证：全量 unittest + diff 检查；按 AGENTS 执行角色 check 并如实记录 A 无匹配角色导致的失败，不修改检查器绕过范围检查。
