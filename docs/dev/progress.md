# 开发进度看板

> 由 A 总控维护当前快照；各角色的可审计开发明细见 `docs/dev/handoffs/`。本页不是自动状态源，也不替代 Git、测试日志或角色门禁。

## 当前快照

- 更新时间：2026-10-23 02:18 CST（Asia/Shanghai）
- 总控角色 / 分支：A integration / `codex/integration`
- 当前发布基线：`56882a7`（权威结果统计与稳定分页预览已验收）
- 当前规范任务：`docs/dev/tasks/grouped-expansion-preview.json`
- 总体状态：IN_PROGRESS（主标签/辅助标签扩词分组预览）；不代表 v1.9 完整产品验收。
- 已完成：受信检测器发布配置加载、启动预检、制品与模型摘要、脱敏证据；清洗批准已绑定 `detector_release_digest`，core 账本持久化并兼容旧记录；运行时发布漂移会在素材读取前失败关闭。
- 正在进行：冻结主标签交付单元、辅助检索词、独立打包与跨组去重规则。
- 当前阻塞：无。
- 下一步：实现分组扩词预览、确认卡持久化和页面展示；不把辅助词作为独立包。
- 本地验收：8080 页面显示“结果尚未发布”，并明确未发布当前版本权威结果集时不展示模拟素材或推算数量。
- 本轮验证：结果视图 HTTP/UI/model 定向 12 项 PASS，JavaScript 语法 PASS；integration 门禁 SCOPE PASS、431 项 PASS、LOCAL GATE PASS。首次定向运行因测试夹具写错 `ResultReader` 赋值位置失败，修正后真实通过；未在门禁外额外运行完整回归。
- 本地验收：8080 服务已从发布基线重启；浏览器确认正式任务显示 revision 1 / generation 1、阶段 PLANNING、目标 3、五个节点、有效数量与缺口“未知”，并显示“不展示估算百分比”。
- 本轮验证：进度 HTTP/UI/model 定向 11 项 PASS，JavaScript 语法 PASS；integration 门禁 SCOPE PASS、425 项 PASS、LOCAL GATE PASS。按用户要求未在门禁外重复完整回归。
- 本轮验证：HTTP/UI/core 定向 11 项 PASS，JavaScript 语法 PASS；integration 门禁 SCOPE PASS、420 项 PASS、LOCAL GATE PASS；浏览器本地合成会话/确认关联 PASS。未在门禁外重复完整回归。
- 本轮验证：`tests.test_conversation_contracts` 5 项 PASS；integration 门禁 SCOPE PASS、415 项 PASS、LOCAL GATE PASS。未在门禁外重复完整回归。
- 最近验证：历史 HTTP/UI/core 定向 10 项和 JavaScript 语法检查通过；integration 门禁 SCOPE PASS、完整 410 项通过、LOCAL GATE PASS；浏览器显示 2 条 SQLite 历史任务并成功打开 revision 1 任务继续反馈。完整 `conversation_message` 草稿历史、真实 W3/内网 NOT_RUN。

## 更新规则摘要

- 活跃开发至少每 30 分钟更新；开工、关键里程碑、阻塞/恢复和交付立即更新。
- A 更新本页；B/C/D 只更新各自 handoff，由 A 汇总。
- 无变化也记录原因；测试计划、RUNNING、PASS、FAIL、NOT_RUN 分开表述。
