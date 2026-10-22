# 开发进度看板

> 由 A 总控维护当前快照；各角色的可审计开发明细见 `docs/dev/handoffs/`。本页不是自动状态源，也不替代 Git、测试日志或角色门禁。

## 当前快照

- 更新时间：2026-10-22 22:33 CST（Asia/Shanghai）
- 总控角色 / 分支：A integration / `codex/integration`
- 当前发布基线：`1606e5812910a3b19d9b8f17e4b95556f86f5031`
- 当前规范任务：`docs/dev/tasks/released-cleaning-approval-entry.json`
- 总体状态：READY_FOR_REVIEW；代码仍是 v1.9 的 P0 开发切片，不代表完整产品验收。
- 已完成：受信检测器发布配置加载、启动预检、制品与模型摘要、脱敏证据；清洗批准已绑定 `detector_release_digest`，core 账本持久化并兼容旧记录；运行时发布漂移会在素材读取前失败关闭。
- 正在进行：受信批准准备入口已完成并通过验证，等待本地提交和快进集成。
- 当前阻塞：无。
- 下一步：提交并快进 `master`；后续优先补发布配置到服务启动装配的实际应用入口或下一项 P0 缺口。
- 最近验证：定向 15 项、完整回归 391 项、integration 门禁内部 391 项均通过，SCOPE PASS / LOCAL GATE PASS；真实 DJ、真实模型和内网联测 NOT_RUN。

## 更新规则摘要

- 活跃开发至少每 30 分钟更新；开工、关键里程碑、阻塞/恢复和交付立即更新。
- A 更新本页；B/C/D 只更新各自 handoff，由 A 汇总。
- 无变化也记录原因；测试计划、RUNNING、PASS、FAIL、NOT_RUN 分开表述。
