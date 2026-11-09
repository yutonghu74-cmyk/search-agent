# 当前结果只读视图

`GET /v1/retrieval/tasks/{task_id}/results` 只读取已经由业务账本发布、且仍绑定当前 task/revision/generation/task_version 的结果集。`view` 支持 `accepted`、`reserve`、`failed`、`review` 和 `user_excluded`，`limit` 为 1–100；游标绑定不可变 `result_set_digest`、视图和偏移，不能跨结果版本或跨视图复用。

没有当前权威结果集时返回 `available=false`，除确认方案中的目标数量外，结果统计均为 `null`，`items` 为空。这不等于零结果、检索完成或清洗通过。读取器仅把 `NOT_FOUND` 转为该空态；撤权、旧 generation、版本冲突和存储错误原样失败关闭。

当前条目公开受限素材标识、冻结清洗结论、规则短原因和受权预览能力，不返回内容摘要、文件路径、私有地址或二进制。图片读取必须经授权媒体网关逐次复验；分页读取本身不授予素材访问权。

`POST /v1/retrieval/tasks/{task_id}/manual-reviews` 只允许任务发起人处理当前 `review` 视图中的 model-unknown 素材。请求必须同时绑定 `expected_task_version`、`result_set_digest`、`candidate_id`、`asset_version`、`action=accept|reject`、非空 `reason` 和幂等键。裁决生成新的不可变结果版本并重算 accepted/reserve/failed/pending_review/gap；旧结果、旧游标和已打包结果不能被改写。分组配额任务的人工通过必须已有至少一个通过的组归属证据，否则关闭失败。

人工裁决是独立审计层，不覆盖或伪造模型规则证据。`accept` 将素材加入权威去重和配额分配，`reject` 计入人工裁决后的淘汰结果；两者都将 pending_review 减一。已发布交付包禁止继续裁决。
