# 公共 Agent 阶段规划 API

## 当前范围

提供检索/清洗 Agent 的准备、审阅确认、启动、查询和取消闭环。适用于已经有正式、已确认 agent_requirements 的任务；不从旧任务临时推导自主授权，也不解除 TaskService 对新 Agent 模式正式任务创建的现有门禁。

当前按 [框架完善、检索优先、清洗独立](../docs/architecture.md#17-后续实施顺序) 推进。`harness-routes@2` 可只装配检索角色，无需安装/配置清洗运行器；旧 `harness-routes@1` 保留原两个角色要求。纯检索可显式使用 `agent-requirements@2` 的空 processing，不能请求无处理需求的清洗阶段。

本片不把规划结果直接派发为检索/DJ作业，execution_allowed 始终为 false。finished 只表示有效提案/报告已保存，不表示素材已召回、筛选通过或打标完成。真实业务执行适配、标签生产与交付、正式任务准入及前端操作卡继续后续开发；本轮没有启用现场服务。

## 路由

基础路径 `/v1/retrieval/tasks/{task_id}/agent-stages`，身份沿用现有 API 层；生产使用既有验证身份和项目授权，不以开发者头代替生产授权。

只读发现路径：`GET /v1/retrieval/tasks/{task_id}/agent-capabilities`，不接收查询参数或 POST。返回当前任务/项目可选的规划角色、角色/运行发布和输出契约，以及 `requirements_confirmed`；没有对应处理需求时不展示清洗角色。`availability=configured_not_probed` 明确区别配置与实际运行能力，`execution_allowed=false`。发现不 probe、不建草稿/run、不调用模型，不返回上下文/路径/凭据；未授权任务不可见。共享集成主体的原模块白名单不因本路由扩权。

| 方法与后缀 | 行为 |
| --- | --- |
| GET 基础路径 | 当前revision/generation/task_version的阶段元数据列表，limit默认32、上限100 |
| POST /prepare | 创建审阅草稿；不创建run、不预留模型额度、不调用模型 |
| GET /{stage_draft_id} | 查询持久状态及事件页，after_cursor默认0、每页至多100事件；不派发、不续租 |
| POST /{stage_draft_id}/confirm | 发起人确认展示的摘要；批准和草稿状态同事务保存，不启动模型 |
| POST /{stage_draft_id}/start | 明确启动已确认的规划；入队和run/attempt标识同事务保存，之后通过T05派发 |
| POST /{stage_draft_id}/cancel | 取消阶段、撤销对应当前批准/租约，再停止本进程持有的运行资源；预算不退还 |

所有POST要求Idempotency-Key。未注入agent_planning服务时返回AGENT_PLANNING_UNAVAILABLE；browse-only实例继续拒绝所有POST。原agent-runs审计接口及检索/清洗接口保持原行为，不被此接口替换。

完整接口见 [OpenAPI](agent-stages.openapi.yaml)，主OpenAPI以外部路径引用接入；不要在两个文件复制同一Schema。

## 准备与确认内容

prepare严格接受：

```json
{
  "role": "retrieval",
  "model_profile_ref": "registered-model-profile",
  "model_calls": 2,
  "expected_revision": 1,
  "expected_generation": 1,
  "expected_task_version": 1
}
```

role为retrieval或cleaning；版本字段必须为正整数，布尔值不能当数字。模型必须是后端为该用户授权的不可变配置引用；URL、凭据、上下文、来源/检测器引用、物理路径和Harness配置均不能由请求指定。model_calls不超过已确认需求的上限。

后端context_provider(actor,task,role)提供授权、最小、有界输入；模型语义、角色版本及运行发布由后端解析。准备结果只返回用户可审阅的角色/模型引用、运行发布引用、输入摘要、调用分配额、时间上限、当前任务版本及proposal_digest，不返回提示全文、context_json、SDK会话、密钥或本地路径。

阶段确认/启动/取消严格接受三个字段：action（必须对应当前路由）、proposal_digest（prepare返回的值）、expected_task_version。审阅摘要覆盖角色、运行发布、模型语义、冻结需求、输入、额度和时间策略。正式阶段绑定不由客户端构造。

确认和首次启动前会重新检查输入以及角色/模型/运行配置；变化返回AGENT_INPUT_CHANGED或AGENT_STAGE_CONFIGURATION_CHANGED，要求重新准备审阅。实际执行使用持久化的原绑定，不因配置更新静默切换。已启动尝试的重复start不重新编译或分配run。

## 持久化及幂等

新增harness_stage_drafts及harness_stage_actions，通过StageReviewStore端口访问。SQLite实现与HarnessJournal/业务Task同库：

- 同prepare键/正文返回同草稿；同键异体冲突。准备重放不重新构造输入，也不产生额外模型调用。
- 不同确认键对同一审阅只批准一次；不同启动键也只建立同一run/attempt。
- 审阅状态与批准、启动状态与入队均为单事务，不能留下“已确认但批准缺失”或“已启动但run未记录”的提交间隙。
- 动作键按用户/任务/动作隔离，不能用于另一个草稿或不同正文。
- 当前任务、项目权限、版本和代次在写入事务内重验；列表也在读事务内重新读取当前任务版本，避免列表显示已经过期的一代。
- 审阅JSON有版本和摘要校验，损坏或未知格式拒绝读取，不重建或重算为新的批准。
- 任务/对话硬删除沿用原合法入口，级联删除审阅与动作回执；不会放宽conversation-linked任务的删除限制。

GET和列表只读取持久状态。原进程不在时仍可查询完成结果；后台心跳负责终态保存，前端查询不会触发模型、确认或续租。已启动但未知的尝试继续遵守T05恢复规则，不由HTTP重试自动重跑。

## 装配

```python
from dsh_retrieval.adapters.harness.composition import build_agent_planning_service

planning = build_agent_planning_service(
    task_service, harness_routes_config,
    authority=read_current_authority,             # (actor, invocation) -> StageAuthority
    context_provider=build_authorized_context,   # (actor, task, role) -> frozen JSON object
    model_environment=resolve_model_environment, # (actor, immutable_profile_ref) -> environment
    model_binding=resolve_model_binding,         # (actor, immutable_profile_ref) -> semantic digest
    project_routes=resolve_project_routes,       # optional: (actor, task) -> {enabled role: installed release ref}
)
handler = create_handler(task_service, agent_planning=planning, ...)
```

上述函数是后端函数，不是配置中的任意import路径。背景运行时也须解析当前授权，不应捕获或长期保留HTTP请求头、临时会话凭据。authority/context_provider负责目录、媒体和检测能力的当前授权；journal再检查已批准阶段、业务版本和租约。未经验证的Capability不能靠字符串自报为支持。

`project_routes` 只在后端已启用角色和已安装发布目录中选择，负责真实项目授权及可见性。空映射明确禁用，不回退默认角色；未传回调保留原静态路由。按每个当前任务解析，不能按用户缓存项目选择。确认/首次启动前绑定变化要求重新审阅，已入队尝试用账本原发布；部署需保留旧适配器可解析，不允许用新版本冒充旧版本。当前提供的是可信配置接入点，不是自助注册/发布/绑定门户。

默认装配使用现有业务SQLite；生产可同时传入其他HarnessJournal/StageReviewStore端口，必须提供同一原子账本的storage_identity。应用服务不导入厂商SDK或SQLite实现。不能注入内存Runner到公共API，也不能只给一个持久化端口；新运行默认目标仍按rc.5路由配置确定。

关闭服务时调用planning.close；关闭不重置预算。现场平台入口尚未自动注入此服务，配置目录和凭据必须按既有部署机制显式装配，本次测试没有部署或重启现场。

## 验证证据

服务与HTTP用例覆盖两个角色、准备不运行、并发确认/启动一次生效、跨重启准备/查询、同键异体、scope/版本/输入/配置变化、取消、列表、损坏审阅及删除级联。实际rc.5通过公共HTTP准备→确认→启动→查询完成，使用临时本机模型接口；准备时模型请求为0，完成提案共2次，替换服务实例查询后仍为2次。

相关API/Harness/SQLite/事务/旧契约/旧DSH组169项定向通过、无跳过；新的显式装配入口另以实际rc.5 HTTP闭环单项复验通过。不是完整回归、真实模型语义质量或内网/生产验收。

后续业务适配首片见 [检索提案桥接](agent-retrieval-handoff.md)：已完成的阶段策略可经服务端接口编译到既有单计划账本，但该 HTTP API 不会自动确认或派发检索，正式任务准入仍关闭。

清洗侧的后端 filter 桥接见 [清洗提案桥接](agent-processing-handoff.md)。阶段上下文必须冻结 manifest_ref；当前 API 仍不自动确认或执行清洗，tag/rank/analyze 尚需输出执行器。
