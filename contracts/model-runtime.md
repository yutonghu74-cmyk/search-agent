# LLM/VLM 接入与选择

启动参数 `--model-config /absolute/private/models.json` 指定服务端 JSON：

```json
{"models":[
  {"id":"planning","kind":"llm","provider":"your-provider","model":"your-text-model","base_url":"https://example.invalid/v1","api_key_env":"DSH_LLM_KEY"},
  {"id":"vision","kind":"vlm","provider":"your-provider","model":"your-vision-model","base_url":"https://example.invalid/v1","api_key_env":"DSH_VLM_KEY"}
]}
```

实际地址与密钥不得入库；配置仅引用环境变量。协议沿用现有聊天兼容协议 `/chat/completions`，要求 JSON 输出，VLM 要支持 image_url data URI。不是所有服务均兼容，必须实测。只接受 HTTPS，不跟随重定向；单次超时 60 秒，响应上限 1 MB。HTTP 429/502/503/504 和临时连接故障最多重试 2 次，等待 1、2 秒起，数字型 Retry-After 最多尊重到 5 秒；其他 HTTP 错误、无效 JSON、截断与 schema 错误不重试，不切换模型。端点、凭据及上游错误正文不进入目录/结果。

项目授权的 `GET /v1/retrieval/projects/{id}/models` 返回模型 ID、角色、provider/model。无配置返回空目录，界面明确未配置，不提供假模型。当前目录为服务端部署级、已获该项目访问权者可见，不是独立多租户模型授权系统；部署只注册该环境获准发送数据的服务。

扩词请求可增加 `model_selection:{llm,vlm}`；角色必须匹配。无分组时可同时传 `target_count`（整数 1–10000），服务端按 `min(30,max(1,ceil(target_count/100)))` 计算每个明确类别的建议辅助词数，并将数量与“按类别分别扩词、不扩大否定范围”的指令发给 LLM。建议数不是硬配额，模型可按语义覆盖增减；仍遵守每类最多 30 个、最多 20 类的方案容量与去重校验。用户可传 `feedback_text` 与当前 `current_groups`，让模型基于原需求和当前词表重新扩词；若明确要求词数，可附 `requested_terms_per_group`（1–30），该数优先于张数/100，模型返回每组恰好该数量，否则拒绝结果。反馈排除情绪/主观描述时指令约束词表使用可观察主体、姿态、动作或场景。有手动分组时不重复调用且不接受上述反馈参数。正式任务的后续消息先经绑定任务版本的 `/followup-intent` 模型语义分流，返回 `expansion/cleaning/clarify` 及新消息中明确的数量；不依赖“扩词/召回词”关键词，也不把分类失败默认为清洗。`expansion` 先生成词表预览；用户确认后以同一 task ID 发布新版词表、目标数量和新 revision/generation，旧任务在确认前不变。grouped_expansion 新增可选 model_binding={llm,vlm,config_digest,prompt_version}，纳入计划及草稿摘要；本版指令绑定 `retrieval-models@2`，LLM 生成仍标记 llm@1。旧模板计划不变。

ModelRuntime.assess 为后端 VLM 接入点：接受已授权、已验证的有限 PNG/JPEG 字节和需求，不接受网络图片地址。头标记检查不是完整解码验证，调用方仍需做媒体完整性检查。模型返回 pass/fail/unknown 和短理由；不合法输出/调用失败抛错误，绝不转成 pass。配置或指令版本变化拒绝复用旧 binding。结果不改变有效计数，不授予业务通过。

反馈清洗建议返回 `feedback_status=ready|needs_clarification|unrelated`。仅 `ready` 可携带候选 `requirement`；其他状态必须留空并提示用户补充或改写，不能用原需求冒充新指令。建议是独立预览，不在反馈草稿确认摘要内；当前确认只能保存反馈文本为新需求版本，不能批准或运行这条 VLM 指令。界面默认只展示用户能采取的下一步，模型、摘要等技术字段不作为主对话内容。

已串通 Chat 选择→LLM 扩词→用户修改/确认→历史模型绑定、查询编译，以及选定 VLM 的请求/判定适配接口。尚未把 VLM 接到已批准清洗 worker/ReleasedDetectorRegistry，不能把此接口冒充受信发布检测器；检索到素材→授权网关→VLM→权威清洗结果写回仍需装配和真实环境验证。当前只用注入模拟传输联测，未发送真实业务图片到外部服务。
