# 主标签与辅助检索词分组

检索 Agent 词表阶段 `retrieval-terms@1` 已接入原队列与模型端口。规范路径为 `/v1/retrieval/projects/{project_id}/dialogue-sessions/{session_id}/retrieval-agent/terms` 与 `/v1/retrieval/tasks/{task_id}/retrieval-agent/terms`，支持原 GET/POST、`/{job_id}` GET 和 `/{job_id}/retry` POST；以下 `expansion-jobs` 路径保留为同一账本的兼容别名。响应新增 `agent={capability:retrieval, phase:terms, workflow_ref:retrieval-terms@1, implementation_ref:grouped-terms-agent@1}`，原状态/进度/result结构保持。词表阶段不是正式 Agent 执行 run，不新增 Task 或批准。完整接口见 [OpenAPI](retrieval-terms.openapi.yaml)。

2026-11-26：新窗口由统一`retrieval-agent@1`拥有规划和执行。完成规划返回`phase=waiting_confirmation`及确认模板，明确确认`POST .../retrieval-agent/terms/{job_id}/confirm`后才创建正式任务、归档原会话并继续同一Agent；返回task与原规划作业/摘要关联。确认后仍可从原规划地址读取执行阶段。重复点击/重试复用同一任务，旧需求、旧词表、模型/权限失效不得确认；确认回执丢失不能覆盖后来修改的新需求。新响应`agent`为统一身份，原词表适配器描述保留在`phase_adapter`；旧记录/旧接口兼容。见[实施及验证](../docs/dev/reports/retrieval-agent-unification.md)。

编辑入口也接受 {dimension_id, parent_label, groups:[{primary_label, auxiliary_terms}]}，输出 generator=user-edited@1。辅助词可为空，表示仅保留主标签；不自动补齐固定数量。用户预览后可以返回修改，新方案生成新摘要，正式确认沿用草稿摘要与版本校验。持久化后的分组在历史正式版本中展示。分组配额语义见下文；已确认组配额的多组任务按组目录打包（`image-delivery@3`，见 `contracts/image-delivery.md`）；未确认各组目标的多组任务的打包准备仍明确拒绝。

`grouped-expansion@1` 将“交付分类”和“召回用词”分开：每个 group 的 `primary_label` 是独立交付单元，`package_name` 必须与主标签相同且 `package_mode=separate`；`auxiliary_terms` 只用于建立后续查询分支，不创建独立结果包，也不单独占用交付数量。

本地预览只识别用户明确写出的 `主标签扩N个词`，N 为 1–200，最多 20 组。例如 `动物，狗扩20个词，猫扩30个词` 生成上级标签“动物”下的狗、猫两组。没有明确语法时返回 `recognized=false`，不从普通自然语言猜测分类；当前 `local-template@1` 只是可审阅的确定性词表组合，不代表模型扩词质量。

正式任务的单组反馈若给出完整带引号的词表（首项必须等于已确认主标签、数量与原话一致），或明确把一个已存在的辅助词替换成新词，可直接生成 `user-edited@1` 待确认提案而不调用扩词模型。纯“检索词改成/减少到 N 个”按原词表顺序缩减；原词表不足时仍走模型生成。含其他语义修改、歧义或版本冲突时不得套用确定性快路；发起人确认前正式 revision、检索和清洗均不改变。

默认每组 `delivery_target=null`，因为“扩 20 个词”不是“交付 20 张图片”。系统不得平均拆分总目标；只有用户原话按方案已有组标签逐组写明张数（如“狗 20 张、猫 30 张”或“20 张狗和 30 张猫”）且总和等于 `target_count` 时才写入正整数目标，任一组缺失、歧义或总和不符都保持全部为 `null`。目标必须全部设置或全部为空，并受 `plan_digest` 保护；确认词表前页面逐组展示目标，未分配时明确提示只按总数计数。部署可设 `DSH_GROUP_QUOTAS=off` 关闭能力，此时含配额的提案返回 `QUOTA_NOT_SUPPORTED`，不会静默丢弃。`overlap_policy=single_primary_per_aggregate` 表示同一素材即使带多个标签，在聚合总数中默认只计一次；可生成额外聚合清单，但不复制素材文件或重复累计总数。

`POST /v1/retrieval/projects/{project_id}/expansions` 需要可信身份和项目创建权限，只返回待确认预览。正式草稿保存完整分组及 `plan_digest`，篡改标签、辅助词、包名或策略会导致验证失败。预览和确认都不启动检索、清洗或打包。

后台扩词（新 Chat 与正式任务反馈）分别使用 `POST /v1/retrieval/projects/{project_id}/dialogue-sessions/{session_id}/expansion-jobs` 和 `POST /v1/retrieval/tasks/{task_id}/expansion-jobs`，返回 HTTP 202 及 `job_id`；同一路径 GET 返回最近作业，追加 `/{job_id}` GET 查询指定作业，`/{job_id}/retry` POST 仅重试失败批次。响应包含 `status=queued|running|failed|completed`、`completed_terms`、`target_terms`、`failed_batch`、错误及完成后的 `result`；未完成时 `result=null`，不暴露部分词表为可确认提案。正式任务提交必须带 `expected_task_version`、`expected_revision`，Chat 提交必须带 `dialogue_session_id`、`dialogue_version`；所有读取和重试均校验原发起人访问。作业绑定模型配置摘要，版本或配置变更后不发布旧结果。重复提交同版本同请求返回同一作业，显式重新生成需新 `regeneration_key`。旧同步预览接口保留兼容，不用于页面的大词表重新扩词。
