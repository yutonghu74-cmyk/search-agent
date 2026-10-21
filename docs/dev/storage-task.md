# 本地恢复切片任务卡

A 总控在 codex/integration / var/worktrees/integration，基线 1020e00。用户已授权本地开发、验证后提交/合并，不推送、部署或接真实数据。

先发布本任务卡，再同步 core 独立 worktree 开发仓库适配器。core 分支 codex/task-core，范围 src/dsh_retrieval/adapters/task_storage/、tests/core/、docs/dev/handoffs/core.md；公共接口保持现有 TaskRepository/CleaningRepository。SQLite 使用 Python 标准库，仅本地开发实现，不替用户决定内网正式账本。

实现 SQLite 单快照账本适配器：复用已测试内存状态转移，但每个仓库调用在独立数据库事务中加载、修改、提交。JSON 白名单类型编码（禁止 pickle），保留草稿、任务、幂等、清洗 operation、generation 栅栏、结果指针及内存事件。写事务跨连接串行化；未知格式或坏数据拒绝读取，不能重置空库；失败完整回滚。整快照方式只适用于小规模本机验证，非生产数据库设计或 outbox。

验收：关闭实例/新进程恢复；不同连接并发确认一次；取消栅栏跨实例有效；持久化失败回滚；未知格式与损坏拒绝；身份隔离。沿用 core 角色门禁，不修改责任表。

core 集成后 A 负责演示装配与恢复入口，单独回归。恢复仅针对已准备的固定合成清洗方案；取消/暂停不自动重跑。候选来源恢复仍验证原始文件摘要，不承诺暂存文件被清理后的可恢复性。完整生产恢复、任务级 outbox/调度、内网数据库联测不在本切片内。
