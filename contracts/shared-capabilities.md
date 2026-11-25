# 外部 Agent 复用检索/清洗：首个后端闭环

除下文直接模块调用，现有确认自主任务还可显式装配 [统一 Agent 作业服务](agent-jobs.md)，通过 AgentJobClient 复用单页检索完整作业。它有独立的 agent.retrieval.read/prepare/start 授权，确认仍仅发起人；不自动升级旧任务或放开正式创建门禁。

本片复用既有任务、检索、候选清单、清洗确认和执行接口，新增外部任务关联、调用权限边界、调用回执和 Python SDK。团队可保留自己的 Agent/多 Agent 编排与 Harness；平台不要求启动内置 Agent。清洗仍使用当前发布的 filter 能力，未接通标签执行。

## 已接通的流程

1. 发起人通过原草稿接口审阅并确认正式任务。草稿阶段不创建正式 task/batch/run。
2. 后端向集成主体授权项目、具体 task_ids 和模块能力。外部 Agent 用自己的凭证，将 external_task_ref 关联到已确认任务。
3. 外部 Agent 准备检索方案；发起人审阅并确认。共享模式下 manual 检索节点不会因任务确认自动派发，后台派发同样遵守人工节点。
4. 原检索后台产生候选，外部 Agent 查询候选/清单；发起人确认输入清单。
5. 外部 Agent 对清单准备发布清洗配方；发起人审阅并确认，原清洗后台执行。外部流程查询实际作业状态和结果，继续自己的生产/训练流程。

SDK 的 AgentClient 不提供确认方法；OwnerClient 用于发起人的显式确认操作。两者的区别也由服务器执行：集成主体调用旧确认、创建任务、任意其他路由或尝试用 OwnerClient 代批，都会被拒绝。仅隐藏 SDK 方法不是权限控制。

## 后端装配与身份

`create_handler(..., identity_mode='platform', identity_provider=..., shared_access=...)` 显式启用共享模式，仍要求既有 PlatformProjectAccess 验证主体/项目。模拟 X-User-Id/X-Tenant-Id 不能启用此入口。

`SharedAccess(bindings, resolve_principal=...)` 中的回调由部署方根据已核验的凭证类型、委托和集成登记返回 `SharedPrincipal`。它接收已认证 Actor 和当前请求头；不能根据请求自报的身份类型或能力头授予权限，不得未查到集成登记就回退为 human。回调返回的 actor/project 必须与当前已核验主体/项目一致。实际平台凭证类型/委托字段仍需内部确认，本片没有猜测或实现未核验的 JWT/服务账号协议。

- human：仅需求发起人可使用原确认接口，继续执行原项目/版本/摘要校验。
- integration：固定 integration_ref、项目、具体 task_ids 及能力集合；调用方不能自行传入这些权限。任务必须属于已核验的发起人/合法委托主体。
- 能力包括 retrieval.read/prepare、cleaning.read/prepare/execute、results.read、task.cancel，以及显式 agent.retrieval.read/prepare/start 和 agent.retrieval.terms.read/prepare。每次请求重新解析主体和检查项目授权，绑定 ID 不是访问凭证；任一能力都不授予人工确认权。

团队身份由后端集成登记映射为 integration_ref，当前没有团队自助注册门户，也没有自动把同一项目的其他任务授权给集成。外部 Agent 不得持有发起人的人工确认凭证。

开发存储为 `SQLiteExternalBindings(repository, projects, max_bindings=1000)`，必须与业务任务共库。新 external_task_bindings/actions 表只存关联/调用回执，不复制正式任务/审批/结果模型；两条硬删除路径都级联清理，未知表仍拒绝。max_bindings 是关联数量上限，不是 GPU/费用/素材调用预算；检索/清洗沿用已配置的原业务资源限制，跨团队聚合预算仍待 AP12。

存在 SearchDispatchCoordinator 时，须在启动前用 `respect_manual_retrieval=True` 构造。共享 handler 拒绝装配忽略人工节点的派发器。未启用共享入口的旧实例保留既有行为；manual 任务的自动派发事件只记录人工等待，发起人后续明确确认检索计划后才由原入口入队。

## 接口与回执

新增接口见 [OpenAPI](shared-capabilities.openapi.yaml)：

| 方法/路径 | 作用 |
| --- | --- |
| GET /v1/shared/capabilities | 当前集成能力与后端装配状态，不代表依赖健康/效果验收 |
| POST /v1/shared/tasks/{task_id}/binding | 绑定 external_task_ref 与正式任务当前 revision/generation/需求摘要 |
| GET 同一路径 | 查看关联和 stale 标记；不派发 |
| GET /v1/shared/tasks/{task_id}/receipt?operation=…&key=… | 查询准备/执行调用回执；不自动重做 |
| POST /v1/shared/tasks/{task_id}/cancel | 取消整个绑定任务的发布权限，不只是解除关联 |

其他模块接口沿用原 `/v1/retrieval/tasks/{task_id}/...`，集成请求须携带 X-External-Task-Ref；服务端核验归属及当前代次，且仅开放能力对应的固定路由。原始 tick、增量重算、上传、管理和确认接口不在此片集成工具范围内。人工步骤继续使用原确认摘要/expected_task_version/Idempotency-Key。

retrieval.prepare、cleaning.prepare 和 cleaning.execute 的调用意图先持久保存，模块调用在事务外执行，返回后再次检查当前绑定再保存回执。相同键/正文复用回执，同键异体拒绝；重启后也不重跑已记录的调用。没有完成回执的意图显示 pending_or_unknown，相同键拒绝自动重做。它可能仍在运行，也可能进程中断，不能当作已失败或免费；先查询模块作业/原提案，人工或上层明确决定新的尝试。两阶段回执不冒充跨外部系统的统一事务或 exactly-once 计算。

回执中旧提案不自动升级到新任务版本，旧确认仍受原版本门禁限制。清洗执行回执可能只表示已通知原后台，最终状态应查询 cleaning/status。结果查询仍由原业务层授权并计算有效数，原始候选数不等于通过数。

取消要求独立 task.cancel 能力和已展示的任务版本。事务内将任务置 CANCELLED 并递增 task_version/generation，旧工作不得发布；保留费用预留与历史结果。返回 publication_fenced=true、termination=unknown，不把取消请求当作已停止远程进程。同键取消只生效一次，不取消后来开始的新代次。

## SDK 与示例

### 单独调用检索 Agent 扩词/改词

召回分析另需显式 `agent.retrieval.recall.read`，`AgentClient.recall(task_id)` 只读取已绑定Task的当前候选覆盖，不检索或调用模型。词表提案可携带该报告的 `recall_evidence_digest`，由后端重新读取核验；不接受客户端报告内容，不能靠普通retrieval.read或terms.read扩权。原词表、上级分类、配额和辅助词数量保留；完整/已映射证据才允许据此生成新草稿，发起人确认后才执行。报告中的accepted_count和corpus_recall为null，不是清洗/评测结论。

已绑定正式 Task 的集成可显式授予 `agent.retrieval.terms.prepare`（生成或重试待确认词表）、`agent.retrieval.terms.read`（查询），普通检索/规划权限不隐式授予这两项。`AgentClient.prepare_terms(task_id, body, key=...)` 要求当前 expected_revision/expected_task_version、原始需求、目标数量、dimension_id=default 与用户授权的模型选择；支持原 current_groups/feedback_text/count_edits。`terms(task_id, job_id=None)` 查询，`retry_terms(..., key=...)` 显式重试失败批次。SDK 不自动重试 POST，不提供词表确认方法。

规范 API 为 `/tasks/{task_id}/retrieval-agent/terms`，兼容 `/expansion-jobs` 使用相同权限校验和同一作业。两条路径均检查根任务绑定、当前项目/用户/版本与模型配置；生成只保存待确认提案，既不创建新 Task/revision，也不执行检索或清洗。发起人仍经原反馈草稿/摘要确认新词表；新 Chat 的确认前会话暂不对集成凭证开放。该阶段使用原作业正文摘要去重与 job_id 查询，未接入 shared receipt 操作目录，不能假定 key 可独立跨不同正文重放。契约见 [词表阶段 OpenAPI](retrieval-terms.openapi.yaml)。

SDK 为 [shared_sdk.py](../src/dsh_retrieval/shared_sdk.py)，仅使用标准库。凭证按请求解析，不进入 URL、日志或错误文本；生产默认 HTTPS、禁止重定向，HTTP 仅可显式允许 localhost 夹具。网络超时可能发生在服务接收之后，因此 SDK 不自动重试 POST。

```python
from dsh_retrieval.shared_sdk import AgentClient, SharedTransport

client = AgentClient(
    SharedTransport(base_url, project_id=project_id, credential=integration_credential),
    external_task_ref=external_training_round_ref,
)
client.bind(task_id, revision=revision, generation=generation,
            task_version=task_version, key="bind-round-1")
preview = client.retrieval_preview(task_id)
proposal = client.prepare_retrieval(task_id, routing_digest=preview["routing_digest"],
                                    choices=selected_choices, key="prepare-search-1")
# 将 proposal 交给发起人审阅；Agent 工具不会自动调用 confirm。
```

[外部流程示例](../examples/shared_agent_workflow.py) 将检索/清洗函数分开，可作为已有多 Agent 框架的工具；它是确定性示例，不假装运行真实 LLM。[人工确认示例](../examples/shared_owner_approval.py) 读取审阅后的 JSON 并明确调用 OwnerClient，凭证来自单独环境变量。脚本只在操作者明确执行对应 phase 时发请求。

示例用法：`PYTHONPATH=src python examples/shared_agent_workflow.py --help`。实际调用要由部署方配置身份、集成授权和真实业务后端，操作文件/输出放在团队自己的工作目录，勿将凭据或真实业务素材写入仓库。cancel phase 使用的 task JSON 必须是操作者确认过的当前版本。

## 验证边界

本机测试使用已核验身份接口的合成响应、独立 SQLite、明确的数值项目检索夹具、真实可解码 PNG 和确定性发布检测器，经真实 HTTP/SDK 跑通人工确认到原执行器及结果查询。原 LOCAL_EXACT 仍限制 demo 项目，未放宽生产限制换取通过。

尚未部署真实平台凭证/项目/目录/模型联测，没有团队自助门户、共享 MCP 执行包装、训练/生产服务或标签账本。外部集成不具备需求或节点确认权限；原发起人可在[原窗口](../docs/dev/retrieval-agent-window.md)确认显式启用项目的有界纯候选任务，其他新模式仍拒绝。本片允许外部 Agent 复用已确认模块任务，不代表完整 Agent 平台验收。
