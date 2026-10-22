# 开发进度看板

> 由 A 总控维护当前快照；各角色的可审计开发明细见 `docs/dev/handoffs/`。本页不是自动状态源，也不替代 Git、测试日志或角色门禁。

## 当前快照

- 更新时间：2026-10-22 23:24 CST（Asia/Shanghai）
- 总控角色 / 分支：A integration / `codex/integration`
- 当前发布基线：`b91007e28fb5106067e08637cf58a19b53dcbcc8`（本地 Chat 提单与反馈体验）
- 当前规范任务：待发布 `docs/dev/tasks/chat-history-core.json`
- 总体状态：IN_PROGRESS；不代表 v1.9 完整产品验收。
- 已完成：受信检测器发布配置加载、启动预检、制品与模型摘要、脱敏证据；清洗批准已绑定 `detector_release_digest`，core 账本持久化并兼容旧记录；运行时发布漂移会在素材读取前失败关闭。
- 正在进行：补充“历史需求”入口；先实现当前创建人在当前项目内的正式任务列表与权限重验。
- 当前阻塞：无。
- 下一步：发布 core 历史任务列表任务卡，完成持久读取后接 HTTP 与 Chat 侧栏。
- 最近验证：UI/API/core 反馈定向 13 项、完整回归 405 项通过；浏览器实际完成 revision 1 提单、revision 2 反馈确认及刷新恢复。补充入口后 UI/API 定向 5 项通过；最终 integration 门禁 SCOPE PASS、内部 405 项通过、LOCAL GATE PASS。真实检索、DJ、W3、模型语义编译和内网联测 NOT_RUN。

## 更新规则摘要

- 活跃开发至少每 30 分钟更新；开工、关键里程碑、阻塞/恢复和交付立即更新。
- A 更新本页；B/C/D 只更新各自 handoff，由 A 汇总。
- 无变化也记录原因；测试计划、RUNNING、PASS、FAIL、NOT_RUN 分开表述。
