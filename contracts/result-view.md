# 当前结果只读视图

`GET /v1/retrieval/tasks/{task_id}/results` 只读取已经由业务账本发布、且仍绑定当前 task/revision/generation/task_version 的结果集。`view` 仅支持 `accepted` 或 `reserve`，`limit` 为 1–100；游标绑定不可变 `result_set_digest`、视图和偏移，不能跨结果版本或跨视图复用。

没有当前权威结果集时返回 `available=false`，除确认方案中的目标数量外，结果统计均为 `null`，`items` 为空。这不等于零结果、检索完成或清洗通过。读取器仅把 `NOT_FOUND` 转为该空态；撤权、旧 generation、版本冲突和存储错误原样失败关闭。

当前条目只公开 `candidate_id`、`asset_version` 和 `preview_status=unavailable`，不返回内容摘要、文件路径、私有地址或二进制。后续预览必须经授权媒体网关逐次复验；分页读取本身不授予素材访问权。

本地 Chat 服务尚未装配执行链结果仓库，因此真实页面默认只能看到“结果尚未发布”。测试读取器仅用于验证已发布结果的统计与分页形状，不代表真实检索或 DJ 联测。
