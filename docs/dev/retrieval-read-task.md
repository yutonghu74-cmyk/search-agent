# 检索作业授权读取任务卡

- A / codex/integration / var/worktrees/integration；契约起点 ecc58429096b94136627eae6b4fcab2e846926e5。
- A 范围：retrieval_contracts.py、ports.py、contracts/retrieval-plan.md、tests/test_retrieval_contracts.py、本任务卡、integration 交接。发布契约提交后交 core，不修改检查器。
- core / codex/task-core / var/worktrees/core；实施基线以 A 新契约提交完整 SHA 为准，在 core 交接登记后开工。
- core 范围：adapters/task_storage/retrieval.py、tests/core/、core 交接。现有存储结构无需改变，新返回模型只是读取投影。
- 验收：v1.9 11.5、13.5；A12/A14/A15/A16/A20/A49 的本地读取子集。待执行与已提交空页可区分；重启无需旧幂等 key 可发现未提交作业；同库跨任务/用户拒绝；旧代次/计划/取消与撤权拒绝；只读不修改任务/事件；授权/存储失败不装成空结果。
- 不含候选清单发布、媒体物化、HTTP、自动恢复调度、事件 relay 或真实内网。允许本地测试、提交及审查后集成；不推送/部署/安装依赖。
