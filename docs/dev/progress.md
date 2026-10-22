# 开发进度看板

> 由 A 总控维护当前快照；各角色的可审计开发明细见 `docs/dev/handoffs/`。本页不是自动状态源，也不替代 Git、测试日志或角色门禁。

## 当前快照

- 更新时间：2026-10-22 23:33 CST（Asia/Shanghai）
- 总控角色 / 分支：A integration / `codex/integration`
- 当前发布基线：`c6410048117578f938a45228c7deb0a57847763c`（历史需求 core 读取）
- 当前规范任务：待发布 `docs/dev/tasks/chat-history-local-ui.json`
- 总体状态：IN_PROGRESS；不代表 v1.9 完整产品验收。
- 已完成：受信检测器发布配置加载、启动预检、制品与模型摘要、脱敏证据；清洗批准已绑定 `detector_release_digest`，core 账本持久化并兼容旧记录；运行时发布漂移会在素材读取前失败关闭。
- 正在进行：core 历史任务读取已通过门禁并集成；正在分派 HTTP 与 Chat 历史侧栏。
- 当前阻塞：无。
- 下一步：接入项目任务列表 HTTP、正式版本历史侧栏和任务切换，完成后重启体验服务。
- 最近验证：历史任务 core 定向 12 项通过；core 门禁 SCOPE PASS、完整 409 项通过、LOCAL GATE PASS。完整 `conversation_message` 草稿历史、真实 W3/内网 NOT_RUN。

## 更新规则摘要

- 活跃开发至少每 30 分钟更新；开工、关键里程碑、阻塞/恢复和交付立即更新。
- A 更新本页；B/C/D 只更新各自 handoff，由 A 汇总。
- 无变化也记录原因；测试计划、RUNNING、PASS、FAIL、NOT_RUN 分开表述。
