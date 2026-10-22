# 五节点只读进度快照

本切片提供当前正式 task 的只读 UI 投影，不创建或修改 workflow node，不派发作业，也不替代未来持久化 `workflow_node_instance`。

- 快照绑定 `task_id / confirmed_revision / task_version / run_generation`，旧 revision 或 generation 不得覆盖新快照。
- 节点固定为检索词扩词、检索、清洗（DJ）、补数、打包；分别返回 `node_status`、`gate_status` 和确认方案中的 manual/auto 模式。
- 只有当前权威 `run_status/stage` 或已进入后继阶段能够证明的节点才显示 running/succeeded。未知 stage 不猜测已完成节点。
- `PLANNING` 表示扩词/选库/计划阶段正在进行，不表示已经检索。清洗结束且为 manual 时只显示 `awaiting_approval`，不会自动批准。
- `counts.target` 来自已确认方案。没有权威结果集时 returned/passed/duplicate/accepted/pending_review/gap 均为 `null`；不计算整体完成百分比。
- HTTP 读取沿用 task 与 revision 的创建人、租户和当前项目权限检查。响应是权威状态的即时投影，不是 SSE 事件历史。

未实现：节点实例持久化、结果分页、统计水位、SSE、断线 sequence、节点批准、暂停/继续/取消和真实检索/清洗进度。
