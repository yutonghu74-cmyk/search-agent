# 开发进度看板

> 由 A 总控维护当前快照；各角色的可审计开发明细见 `docs/dev/handoffs/`。本页不是自动状态源，也不替代 Git、测试日志或角色门禁。

## 当前快照

- 更新时间：2026-10-22 22:40 CST（Asia/Shanghai）
- 总控角色 / 分支：A integration / `codex/integration`
- 当前发布基线：`17e7d8e491f49372e350b0982a3eca013fb7a404`
- 当前规范任务：待发布 `docs/dev/tasks/chat-feedback-contract.json`
- 总体状态：IN_PROGRESS；代码仍是 v1.9 的 P0 开发切片，不代表完整产品验收。
- 已完成：受信检测器发布配置加载、启动预检、制品与模型摘要、脱敏证据；清洗批准已绑定 `detector_release_digest`，core 账本持久化并兼容旧记录；运行时发布漂移会在素材读取前失败关闭。
- 正在进行：启动最小可体验 Chat 提单与反馈闭环；先冻结反馈草稿、确认及版本历史的领域端口。
- 当前阻塞：无。
- 下一步：发布契约任务卡，完成 integration 契约切片；随后分派 core 持久事务，再接本地 Chat HTTP/UI。
- 最近验证：上一切片定向 15 项、完整回归 391 项、integration 门禁内部 391 项均通过；本切片测试尚未运行。真实 DJ、真实模型和内网联测 NOT_RUN。

## 更新规则摘要

- 活跃开发至少每 30 分钟更新；开工、关键里程碑、阻塞/恢复和交付立即更新。
- A 更新本页；B/C/D 只更新各自 handoff，由 A 汇总。
- 无变化也记录原因；测试计划、RUNNING、PASS、FAIL、NOT_RUN 分开表述。
