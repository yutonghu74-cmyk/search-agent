# LLM/VLM 接入与选择

## 对话与规划分离（2026-11-12）

普通提单对话及 `/tasks/{id}/conversation-turn` 使用 `conversation_runtime()` 直接调用已选模型，
不启动 DSH worker，不自动换模型。扩词、目录建议及旧版诊断接口仍沿用配置的规划运行时；
`--agent-runtime direct` 是受支持的规划适配选项，不再仅限旧版诊断。
既有规划引擎缺省仍为 dsh，保持历史模型绑定摘要兼容。`/v1/build` 同时提供
`conversation_engine` 和 `model_engine`。下文 2026-10-24 的“全部经 DSH”描述为历史边界，
与本节冲突时以本节为准。尚未完成两种规划运行时的系统性性能对照。

结构化清洗修改及迁移边界见 [对话架构重构](../docs/dev/conversation-architecture-plan.md)。

提单前 Chat 新增 `dialogue-command` 入口，同步结果沿用 `dialogue-turn@1`。
携带 `Prefer: respond-async` 时先返回 `dialogue-command@1` 回执（处理中为 HTTP 202），
再查询 `dialogue-commands/{command_key}`。命令、会话和消息同账本事务提交；重放完成回执
不调用模型，没有检查点的中断须显式重试。背景线程继承请求身份和受限模型上下文，
不自行切换模型或创建正式业务任务。普通任务的 `conversation-turn` 仍保留同步响应与既有回执恢复。

## DSH 运行入口（2026-10-24）

带 `--model-config` 的 API 启动默认使用 DSH；必须提供 `--dsh-python`（已安装
`requirements-dsh.lock` 的解释器）以及 `--dsh-home` 或显式 `DSH_HOME`。
SDK/native runtime 固定 `0.2.1rc2`，`sdk-minimal` + 仓库受限 patch；版本不符启动失败。
`--agent-runtime direct` 仅为显式旧版诊断模式，DSH 失败绝不自动回退。

对话解析、首次意图解释、扩词、后续意图分类、清洗建议、目录建议均经 DSH 的
Agent loop + pi-ai OpenAI-compatible provider（流式 Chat Completions 和 tool_calls）。
VLM 图像评分仍是独立直接模型算子，不属于第二套 Agent loop。
`GET .../models` 新增 `runtime`，不改变 `items`；不包含地址或凭据。

模型实际可用工具仅 `mcp__retrieval__get_context` 与
`mcp__retrieval__submit_proposal`。后者只提交当前轮候选 JSON，不能创建、批准、
启动或完成任务；调用方继续执行原 schema、语义、权限、版本和确认校验。
工具无任意路径、任务切换或网络参数；不同提案不能覆盖第一次提交。
必须观察到两项成功工具事件及 DSH `completed`，才读取提案；自由文本不能替代工具回传。
关闭 shell、文件、工作目录工具、MCP 资源工具和默认会话/插件信息上传扩展。

每次推理在 principal 哈希目录下新建随机运行目录、DSH_HOME 和 session；使用服务端
选择的当前会话摘要续聊，不直接恢复其他 DSH 会话。audit 关联请求、路由和已授权
dialogue/session version；首轮 session 尚未创建，使用请求 trace 关联。现阶段不把
DSH session 当作业务账本。
运行目录仅本机用户可读；原始 DSH JSONL 含对话数据，不应上传或公开。

运行索引（2026-10-25）：`DSH_HOME/runs.sqlite3`（0600）为每轮记录 run_id、租户哈希、
principal 哈希、project/task/revision/generation/task_version 或 dialogue session/version、
路由、模型 profile ID、状态、错误码和起止时间，不含原文、提示或提案。
`GET /v1/retrieval/tasks/{id}/agent-runs` 先做任务读权限校验，再按同租户返回该任务
最近 200 轮的上述标识与状态（direct 模式 `items` 为空）；这是审计映射，不是任务状态源。
服务启动时把索引中仍为 running 的轮次及旧版 `audit.json` 中的 running 记录标为
`interrupted / SERVICE_RESTARTED`；仅当记录的 pid 当前确为本 worker 模块时才终止其
进程组，pid 已被复用则不发信号。中断轮次不续跑、不接收晚到提案，用户重发即新轮次。

外层整个进程树默认限时 120 秒，超时终止 worker/native runtime/MCP 并拒收结果；
上游重试由 DSH 负责（最多 1 次），业务 schema 修复仍最多 1 次，二者不混为执行重试。
限流/认证/额度/超时仅返回白名单错误码和固定说明，不回传上游正文。
模型绑定摘要额外包含 DSH 版本、受限配置及固定业务指令摘要；迁移前旧绑定不可冒充新绑定。

本切片是 DSH 推理与受限提案入口落地，不是 v1.9 全量工具、生产认证、真实两库
检索清洗或 Skill 审批发布平台验收。生产还需容器/网络出口控制、并发预算、日志保留
与正式身份；进程隔离和移除工具不是完整 OS 沙箱。

启动参数 `--model-config /absolute/private/models.json` 指定服务端 JSON：

```json
{"models":[
  {"id":"planning","kind":"llm","provider":"your-provider","model":"your-text-model","base_url":"https://example.invalid/v1","api_key_env":"DSH_LLM_KEY"},
  {"id":"vision","kind":"vlm","provider":"your-provider","model":"your-vision-model","base_url":"https://example.invalid/v1","api_key_env":"DSH_VLM_KEY"}
]}
```

每行还可选三项请求参数（缺省保持原行为）：`max_tokens`（256–32768；直接调用缺省
3000，DSH 缺省 8000，DSH 经 patch 同步到 provider 模型上限）、`temperature`（0–2，
缺省 0；写 `null` 则不发送该字段，用于拒绝 temperature 的模型）、
`request_timeout_seconds`（5–300；直接调用缺省 60，DSH 单次请求缺省 90，显式配置时
整轮限时至少为该值 +30 秒、不超过 300 秒）。其他键仍拒绝。非缺省参数进入模型绑定
摘要，修改后旧绑定需重新确认；缺省参数不改变既有摘要。DSH provider 当前不发送
temperature。推理型模型较慢、输出长，应实测后再调大这些值。

实际地址与密钥不得入库；配置仅引用环境变量。只接受受信 HTTPS 服务。DSH 协议要求见上节，直接 VLM/旧版诊断入口沿用 `/chat/completions` JSON 输出，VLM 要支持 image_url data URI。不是所有服务均兼容，必须实测。直接调用不跟随重定向；单次超时 60 秒，响应上限 1 MB。HTTP 429/502/503/504 和临时连接故障最多重试 2 次，等待 1、2 秒起，数字型 Retry-After 最多尊重到 5 秒；其他 HTTP 错误不重试，不切换模型。无效扩词 JSON 可进行一次业务修复，非对象响应直接拒绝。端点、凭据及上游错误正文不进入目录/结果。DSH provider 的出口重定向策略尚未单独验收，生产需受控出口。

项目授权的 `GET /v1/retrieval/projects/{id}/models` 返回模型 ID、角色、provider/model。无配置返回空目录，界面明确未配置，不提供假模型。当前目录为服务端部署级、已获该项目访问权者可见，不是独立多租户模型授权系统；部署只注册该环境获准发送数据的服务。

扩词请求可增加 `model_selection:{llm,vlm}`；角色必须匹配。无分组时可同时传 `target_count`（整数 1–10000）。初始每类建议至少 3 个具体且不重复的可见画面辅助词；原文明确类别数量且总和吻合时，服务端按 `min(200,max(3,ceil(category_target/100)))` 分别计算建议数，并对明确类别不足建议数的模型输出进行一次修复重试。未取得可靠类别配额时不把总量复制到每类，使用小规模多样词表；建议数不等于图片数量。用户可传 `feedback_text` 与当前 `current_groups`，让模型基于原需求和当前词表重新扩词；若明确要求词数，可附 `requested_terms_per_group`（1–200），该数优先于默认建议，模型返回每组恰好该数量，否则拒绝结果。每类最多 200 个、最多 20 类，继续执行容量与去重校验。反馈排除情绪/主观描述时指令约束词表使用可观察主体、姿态、动作或场景。有手动分组时不重复调用且不接受上述反馈参数。正式任务的后续消息先经绑定任务版本的 `/followup-intent` 模型语义分流，返回 `expansion/cleaning/clarify` 及新消息中明确的数量；不依赖“扩词/召回词”关键词，也不把分类失败默认为清洗。`expansion` 先生成词表预览；用户确认后以同一 task ID 发布新版词表、目标数量和新 revision/generation，旧任务在确认前不变。实际数量不足只能生成待确认的新词表，不自动扩大已批准检索范围。grouped_expansion 的 model_binding={llm,vlm,config_digest,prompt_version} 纳入计划及草稿摘要；本版指令绑定 `retrieval-models@3`，LLM 生成仍标记 llm@1。旧模板计划不变。

ModelRuntime.assess 为后端 VLM 接入点：接受已授权、已验证的有限 PNG/JPEG 字节和需求，不接受网络图片地址。头标记检查不是完整解码验证，调用方仍需做媒体完整性检查。模型返回 pass/fail/unknown 和短理由；不合法输出/调用失败抛错误，绝不转成 pass。配置或指令版本变化拒绝复用旧 binding。结果不改变有效计数，不授予业务通过。

反馈清洗建议返回 `feedback_status=ready|needs_clarification|unrelated`。仅 `ready` 可携带候选 `requirement`；其他状态必须留空并提示用户补充或改写，不能用原需求冒充新指令。建议是独立预览，不在反馈草稿确认摘要内；当前确认只能保存反馈文本为新需求版本，不能批准或运行这条 VLM 指令。界面默认只展示用户能采取的下一步，模型、摘要等技术字段不作为主对话内容。

已串通 Chat 选择→LLM 扩词→用户修改/确认→历史模型绑定、查询编译，以及选定 VLM 的请求/判定适配接口。本地开发部署可把已确认语义规则接到按任务构造的 `ReleasedDetectorRegistry`、批准候选清单、授权图片网关和可恢复后台 worker；外部真实模型与公司素材环境仍须单独联测，不能用模拟传输结果替代真实验收。

## 已确认任务的真实 VLM 清洗

- `image.semantic-match@1 = true` 是显式硬规则；判定要求取自同一正式版本的
  `query_spec.raw_request`，不得由清洗 HTTP 请求覆盖。
- VLM 配置取自已确认 `grouped_expansion.model_binding`。worker 每次准备和恢复执行都
  重新校验当前用户的模型连接；模型、端点、采样参数、提示词版本或 DSH 运行时绑定
  发生变化时返回 `MODEL_BINDING_CHANGED`，旧批准不继续执行。
- worker 只读取已发布候选清单中的授权 PNG/JPEG 字节，单图上限 5 MB；模型返回只允许
  `pass|fail|unknown`，无法观察或证据不足必须为 `unknown`，不得按通过处理。
- 部署通过 `--local-test-media-manifest` 显式装配本地授权媒体网关和后台清洗调度器；
  未装配时只提供既有只读步骤视图，不声称真实 VLM 已连接。

## 检索期间继续对话

正式任务的 `/followup-intent` 兼容扩展新增 `new_request` 意图，表示另一个独立检索需求，不是当前 task 的 `expansion`。检索期间保持对话输入可用；前端只提示新建 Chat，并将原话预填到新 Chat。旧检索继续后台运行，新正式 task 仍需用户审阅和确认。`new_request` 不返回数量修改操作，也不直接执行。


### Unified task requirement changes

With structured task context, `submit_proposal.kind=requirement` proposes total image quantity
operations (`count_edits`, images only) and optional ordered `cleaning_changes` in one draft.
`requested_terms_per_group` and `expansion_instruction` remain null; `term_count_only` is false.
`target_count` should be null; an echoed absolute count is accepted only when it exactly equals
server computation of the operation. Source-grounded conditions and bounded quantity operations
are validated together against the current bound pending draft. No partial write or approval occurs.
The preview includes `target={before,after}` and `impact.retrieval=rerun|retain_candidates`.
A changed total schedules new-version retrieval only after issuer confirmation. Existing category
quotas require clarification; word/subject plus condition changes are not yet an atomic protocol.
Conversation requests set `parallel_tool_calls=false`; multiple proposals remain invalid.


`requirement` additionally supports optional `retrieval_changes`: an ordered array (1–20 edits)
of `{op: upsert|remove, group_id, primary_label, auxiliary_terms, source_text}`. An upsert supplies
the complete target group terms; null group_id adds a group. Remove requires an existing group_id
and null label/terms. source_text quotes the current user message; a new subject label must occur
in that quote. The service reuses grouped-expansion normalization and reseals its digest while
preserving existing model binding, untouched groups and quotas. It rejects orphaned condition
scopes and remaining conditions requiring a removed subject. All operations share the same atomic
draft and confirmation. `cleaning_plan_preview.retrieval` contains before/after groups and changed.
Pending groups are authoritative for subsequent turns; model-generated terms remain proposals.


### Explicit edits and compact follow-up inputs

For structured task conversations, whole-message absolute/relative image quantities and a complete,
unambiguous quoted single-group term replacement may directly produce a requirement draft. They do
not call a model, load execution status/history, or approve anything. Additional clauses, ambiguous
references, object counts, multi-group scope and unbound browser expansion previews use the existing
model path. Existing quota, revision, source, permission, command and confirmation checks remain.

Initial model input deduplicates raw audit text, words, conditions and derived workflow prose.
Complete current words occur in `current_groups`, condition identities/source/scope occur in
`task_context.cleaning_conditions`, and group identities/quotas stay in the effective requirement
metadata. All recent messages, independent constraints and actual node details remain. The single
existing repair attempt restores the full authoritative input; there is no extra retry or model switch.

Redundant set-count assertions for words are accepted only if one affected group and its complete
proposed list agree exactly; they are discarded before applying changes. `replace` of an existing
retrieval group is normalized to upsert, never to an implicit new group. Removing a cleaning condition
may omit the necessarily-null source_text. A sole literal global subject check in a sole renamed group
follows that explicit subject rename in the reviewed diff; compound/scoped checks are not inferred.
Missing required new-subject checks remain invalid. Character/serialized-request sizes and explicit
path hits are numeric timing counters, not token or billing estimates. Prepared input sizes aggregate
per model-generation round, not per HTTP retry transmission.


Formal task turns support `Prefer: respond-async`. Semantic turns return a durable
`task-conversation-command@1` receipt (202 while active, 200 when already completed/failed);
status and deterministic edits may still return the ordinary turn directly. Poll the existing
command GET route; do not create a new key after a lost response. Async commands share a bounded
per-service worker pool (2 workers, 8 running/queued slots); saturation returns retryable
COMMAND_QUEUE_FULL with the original failed receipt retained. Retry uses the original body/key
and X-Conversation-Retry:true. Synchronous legacy calls and other model job types are not covered
by that async budget. `timings.queue_wait_ms` records queue waiting separately from processing.


### Compact task proposal transport

Direct structured task dialogue uses `submit_task_change` with `{proposal: ...}`. Required `kind` is
requirement/expansion/answer/clarify/status/new_request. Mutating proposals echo the current
`reference_scope` as `scope`. Omit unused fields; business text is generated after validation.

- `images={op:set|add|scale,value}` expresses a single total-image operation.
- `conditions`: add `{op,text,group?}`, replace `{op,ref,text}`, remove `{op,ref}`. c references identify
  current conditions. Existing replace/remove cannot change their category scope. Null/omitted group
  on add is global; supplied g reference must exist.
- `groups`: add `{op,label,words,source}`, replace `{op,ref,label?,words,source}`, remove `{op,ref,source}`.
  Complete word lists and source quotes retain the existing business validation.
- Expansion-only `instruction`, `term_targets=[{group,op,value}]` and `count_only` retain the existing
  planning contract; mixed changes use requirement instead. Read-only/new-request routing cannot carry edits.

Aliases and scope are per invocation, including the single repair attempt; the tool schema enumerates
only current references. Decode restores stable business IDs before existing source/permission/version/
quota/confirmation checks and durable checkpoints. Literal source text is not rewritten. A bounded v1
compatibility bridge accepts only identifiers in the same current table. DSH's explicitly selected
legacy planning adapter keeps its original tool protocol; there is no engine fallback or new agent loop.


### Shared dialogue deadline

A dialogue command has a default 30-second monotonic budget, starting before acceptance/claim
and including in-process queue wait, model HTTP attempts, Retry-After/backoff, and the existing
single proposal-repair attempt. Nested interpretation inherits the same deadline. Model socket
timeouts are capped by the remaining time; response bodies are checked between bounded reads.
An explicit DSH call within this context inherits the remaining worker timeout and its process
tree is terminated by the existing cleanup path. Non-dialogue calls without this context retain
their configured per-model timeout behavior.

`MODEL_TIME_BUDGET_EXCEEDED` is retryable HTTP 504. An expired queued command does not start
model work; a late model response cannot create a new checkpoint. The durable command retains
the request and error; explicit retry of the same key/body starts a fresh budget. A checkpoint
prepared before expiry remains recoverable and may finish its fenced business commit. This is
not a new autonomous approval or execution capability.
