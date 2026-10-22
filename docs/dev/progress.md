# 开发进度看板

> 由 A 总控维护当前快照；各角色的可审计开发明细见 `docs/dev/handoffs/`。本页不是自动状态源，也不替代 Git、测试日志或角色门禁。

## 当前快照

- 更新时间：2026-10-22 23:48 CST（Asia/Shanghai）
- 总控角色 / 分支：A integration / `codex/integration`
- 当前发布基线：`43b07f7`（历史需求 UI 任务卡）
- 当前规范任务：`docs/dev/tasks/chat-history-local-ui.json`
- 总体状态：ACCEPTED（历史需求本地体验切片）；不代表 v1.9 完整产品验收。
- 已完成：受信检测器发布配置加载、启动预检、制品与模型摘要、脱敏证据；清洗批准已绑定 `detector_release_digest`，core 账本持久化并兼容旧记录；运行时发布漂移会在素材读取前失败关闭。
- 正在进行：历史需求侧栏已上线本地体验服务，浏览器任务列表、版本打开和继续反馈入口验证通过。
- 当前阻塞：无。
- 下一步：后续可实现独立 `conversation_message` 草稿会话账本，或继续结果/进度体验；当前正式历史入口已可用。
- 最近验证：历史 HTTP/UI/core 定向 10 项和 JavaScript 语法检查通过；integration 门禁 SCOPE PASS、完整 410 项通过、LOCAL GATE PASS；浏览器显示 2 条 SQLite 历史任务并成功打开 revision 1 任务继续反馈。完整 `conversation_message` 草稿历史、真实 W3/内网 NOT_RUN。

## 更新规则摘要

- 活跃开发至少每 30 分钟更新；开工、关键里程碑、阻塞/恢复和交付立即更新。
- A 更新本页；B/C/D 只更新各自 handoff，由 A 汇总。
- 无变化也记录原因；测试计划、RUNNING、PASS、FAIL、NOT_RUN 分开表述。
