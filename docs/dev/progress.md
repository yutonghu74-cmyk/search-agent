# 开发进度看板

> 由 A 总控维护当前快照；各角色的可审计开发明细见 `docs/dev/handoffs/`。本页不是自动状态源，也不替代 Git、测试日志或角色门禁。

## 当前快照

- 更新时间：2026-10-23 13:32 CST（Asia/Shanghai）
- 当前任务：`docs/dev/tasks/group-editor.json`；A integration / `codex/integration`；基线 `3db96536f6eb76bde6ceda95f0b97b01533f8695`。
- 状态：READY_FOR_REVIEW；可编辑主标签与召回词、返回修改确认卡、历史正式版本分组展示已实现，尚未发布到 8080 体验服务。
- 验证：最新 15 项定向测试 PASS；修复结束加载清空错误提示、确认成功后旧卡仍能返回编辑的问题，新增 JavaScript 行为测试。角色门禁 SCOPE PASS、440 项 PASS、LOCAL GATE PASS（退出码 0）；未在门禁外重复完整回归。
- 页面验收：隔离端口 8081 / 独立 SQLite 账本中，多行分组、零召回词、返回修改、确认及刷新读回分组 PASS；8080 用户账本未改动。
- 未接通：真实模型扩词、检索、分组配额及实际打包，不宣称全项目完成。
- 下一步：按规格书推进较大工作包“查询分支编译及预览”，保留原始查询、去重、词来源及约束引用；随后接现有批准账本与检索装配。不改写既有 RetrievalPlan 模型。发布任务基线、提交与本地合并等待明确授权，已提出询问。

## 前次切片记录

- 更新时间：2026-10-23 02:51 CST（Asia/Shanghai）
- 总控角色 / 分支：A integration / `codex/integration`
- 当前发布基线：`e2d625c`（主标签/辅助标签扩词分组预览已验收）
- 当前规范任务：`docs/dev/tasks/grouped-expansion-preview.json`
- 总体状态：ACCEPTED（主标签/辅助标签扩词分组预览）；不代表 v1.9 完整产品验收。
- 已完成：受信检测器发布配置加载、启动预检、制品与模型摘要、脱敏证据；清洗批准已绑定 `detector_release_digest`，core 账本持久化并兼容旧记录；运行时发布漂移会在素材读取前失败关闭。
- 正在进行：本切片已收口；下一阶段接用户可编辑分组、分组交付配额或真实扩词 provider。
- 当前阻塞：无。
- 下一步：增加用户可编辑分组和每组交付配额确认，再将已确认辅助词编译为真实查询分支。
- 本地验收：8080 输入“动物，狗扩20个词，猫扩30个词”后，页面显示狗 20 词/独立包、猫 30 词/独立包、两组配额“尚未指定”和跨组聚合只计一次；停留在确认卡，未代用户确认正式提单。
- 本轮验证：分组扩词/API/UI 定向 11 项 PASS，JavaScript 语法 PASS；integration 门禁 SCOPE PASS、436 项 PASS、LOCAL GATE PASS。未在门禁外额外运行完整回归。
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
