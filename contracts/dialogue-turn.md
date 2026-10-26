# 未确认需求对话 `dialogue-turn@1`

`POST /v1/retrieval/projects/{project_id}/dialogue-turn` 每轮接收
`session_id`（首轮 `null`）、`expected_version`（首轮 `0`）、`message`、`llm_id`、
`phase`（`request` 或 `terms`），可带 `count_hint`（更多设置里的图片数量）。
词表阶段另可提交 `current_groups`（当前屏幕的主标签/辅助词草稿）和
`current_target_count`（当前草稿图片目标）。这些字段须校验结构及范围，只用于未确认预览，
不代表正式任务、批准或检索状态。理解模型读取这些内容、原始请求、类别数量和最近 20 条双方消息；
模型请求使用真实 user/assistant 角色，最新用户消息独立放在末尾，不再仅作为 JSON 内的一个字段。
服务端先校验项目创建权限，读取绑定租户/用户/项目的独立对话状态，调用受信 LLM
提出类型化动作和一句自然语言回应，再校验字段和数量来源，以乐观版本号写入独立 SQLite 账本。
返回 `session_id`、`version`、`action`、`stage`、`question`、`reply`、`summary`
及尚未确认的 `request`，并可返回 `count_edits`、`expansion_instruction`。
`answer` 动作仅回答、解释或闲聊，保留阶段与需求；回答不能夹带字段修改。
`count_edits` 每项包含 `target: images|terms`、`labels`、`operation: set|add|scale`、
`value`。空 labels 表示全部词组；图片总数的 labels 必须为空。后端在当前草稿上计算并向上取整，
拒绝未知组、同一计数重复操作、非有限值及越界。模型必须逐组明确给出词数操作，
不接受模型用空标签或一条跨组操作模糊指代；扩词端点仍兼容显式客户端的空标签全组操作。
图片 1–10000，每组词 1–30。
`expansion_instruction` 是模型结合上下文消解指代后的修改要求，仍为未批准提案。
`summary` 是供用户审阅的模型摘要，`request.raw_request`
保留当前主题的用户原文及补充消息，不被摘要覆盖。版本或阶段不符返回
`DIALOGUE_CONFLICT`；其他用户不能读取。
模型的 `reply` 只用于表达，不参与审批或执行；后端按已核实的需求状态选择追问/确认，
拒绝执行承诺及与已核实数量冲突的表述。模型未返回可用自然语言时回退到明确的服务端提示，
而不是让自然语言文本成为任务状态来源。格式/操作无效时，最多自动修复一次；重复失败保留原需求，
明确提示生成未通过校验。新记录同时保存用户与助手消息；旧记录无需迁移即可读取。

`GET /v1/retrieval/projects/{project_id}/dialogue-sessions/{session_id}` 只供创建人恢复
当前会话。对话记录不是正式 task/draft/revision/run；接口绝不确认词表或发起检索。
需求摘要的明确认可只进入扩词；词表显示明确确认按钮，只有点击该按钮才使用既有草稿摘要与
创建人确认接口，随后才启动检索。模型将自然语言判为 `confirm_terms` 不等于用户批准。
用户提出新主题时仅替换当前未确认需求上下文，
不修改已确认任务。模型输出异常、数量缺乏依据、权限变化或并发版本冲突均不推进。

本地运行可指定 `--dialogue-ledger /absolute/path/dialogue.sqlite3` 保留重启恢复；
不指定时仅使用内存，不能当作持久试点。模型配置与密钥仍由服务端受信配置提供，
浏览器不提交凭据。真实授权与真实向量检索仍由各自网关负责，本接口不声称完成它们。

## 扩词与已确认任务续聊

`/expansions` 支持 `count_edits`（仅 terms 操作）和 `current_target_count`。
按当前词表计算各组目标，输出不满足目标时最多重试一次；若图片数量变化，可保持词表不变。
词表确认按钮仍是唯一启动入口。浏览器不再用关键词正则解释本轮数量变化。

`/tasks/{task_id}/followup-intent` 保留原版本校验，增加可选 `recent_messages`
和 `draft_preview: {groups, target_count}`，用于理解当前未确认的新词表。服务端任务状态单独提供，
客户端草稿不能覆盖任务版本、权限和运行事实。响应增加 `answer`、自然 `reply`、
`count_edits` 与 `expansion_instruction`；装配本地检索服务时读取当前版本的批准/分页状态和不可用查询原因，
没有证据时保持未知，原始页停止不等于清洗或交付完成。
装配对话账本时，续聊双方消息按租户、发起人、项目和任务隔离保存；刷新/重启后再次续聊读取最近 10 轮，
当前任务事实仍重新从业务账本读取。旧对话库自动新增消息表，无需迁移既有业务任务。
未装配对话账本的测试/嵌入服务只使用有界客户端上下文，不能宣称持久恢复。
