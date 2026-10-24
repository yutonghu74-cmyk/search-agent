# 反馈生成清洗建议（只读预览）

`POST /v1/retrieval/tasks/{task_id}/feedback-suggestions` 接收当前已保存反馈草稿的 `draft_id`、`expected_draft_version`、`proposal_digest` 和服务端模型目录中的 `model_selection={llm,vlm}`。调用者须有该任务及草稿的权限；后端在模型调用前后校验草稿仍为 ready、基准 revision/task_version 未变化。任一漂移返回 `REVISION_CONFLICT`，不返回过期模型建议。未配置模型、无权、歧义输出或上游错误失败关闭。

LLM 只返回有界 `requirement`、`rationale`、`needs_clarification`；响应绑定任务、反馈草稿及模型配置摘要，并标为 `advisory_only`。页面以文本节点展示，不作为可执行代码或系统指令。建议没有存入反馈提案摘要，也没有写入规则、批准配方、发布检测器或业务计数；点击现有“确认新版本”只确认反馈文字，不批准该建议。要使建议成为清洗管线，后续需由用户编辑/确认完整结构化规则，校验受信能力与模型发布绑定，再经清洗批准链路执行。

该接口是本地体验切片，不代表真实检索、真实 VLM 素材清洗、内网联测或 v1.9 反馈重算验收。调用会向所选 LLM 发送原需求及反馈；只有用户已配置并选择模型时页面才请求。
