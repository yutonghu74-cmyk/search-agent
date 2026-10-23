# LLM/VLM 接入与选择

启动参数 `--model-config /absolute/private/models.json` 指定服务端 JSON：

```json
{"models":[
  {"id":"planning","kind":"llm","provider":"your-provider","model":"your-text-model","base_url":"https://example.invalid/v1","api_key_env":"DSH_LLM_KEY"},
  {"id":"vision","kind":"vlm","provider":"your-provider","model":"your-vision-model","base_url":"https://example.invalid/v1","api_key_env":"DSH_VLM_KEY"}
]}
```

实际地址与密钥不得入库；配置仅引用环境变量。协议沿用现有聊天兼容协议 `/chat/completions`，要求 JSON 输出，VLM 要支持 image_url data URI。不是所有服务均兼容，必须实测。只接受 HTTPS，不跟随重定向；超时 60 秒，响应上限 1 MB，不自动重试或切换模型。端点、凭据及上游错误正文不进入目录/结果。

项目授权的 `GET /v1/retrieval/projects/{id}/models` 返回模型 ID、角色、provider/model。无配置返回空目录，界面明确未配置，不提供假模型。当前目录为服务端部署级、已获该项目访问权者可见，不是独立多租户模型授权系统；部署只注册该环境获准发送数据的服务。

扩词请求可增加 `model_selection:{llm,vlm}`；角色必须匹配。无分组时调用 LLM，根据版本化指令输出分组并严格校验；有手动分组时不重复调用。可编辑/确认流程沿用已有草稿。grouped_expansion 新增可选 model_binding={llm,vlm,config_digest,prompt_version}，纳入计划及草稿摘要；LLM 生成标记 llm@1。旧模板计划不变。

ModelRuntime.assess 为后端 VLM 接入点：接受已授权、已验证的有限 PNG/JPEG 字节和需求，不接受网络图片地址。头标记检查不是完整解码验证，调用方仍需做媒体完整性检查。模型返回 pass/fail/unknown 和短理由；不合法输出/调用失败抛错误，绝不转成 pass。配置或指令版本变化拒绝复用旧 binding。结果不改变有效计数，不授予业务通过。

已串通 Chat 选择→LLM 扩词→用户修改/确认→历史模型绑定、查询编译，以及选定 VLM 的请求/判定适配接口。尚未把 VLM 接到已批准清洗 worker/ReleasedDetectorRegistry，不能把此接口冒充受信发布检测器；检索到素材→授权网关→VLM→权威清洗结果写回仍需装配和真实环境验证。当前只用注入模拟传输联测，未发送真实业务图片到外部服务。
