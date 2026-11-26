# 统一 Agent 作业服务与检索首个实现

新增 `coordination / platform-coordinator@1`：既有任务的有界中控规划复用同一作业门面、表和 dispatcher；默认关闭，按部署安装。其 start 仅授权本轮模型规划，不批准子任务；GET/列表只读。支持的检索/清洗动作、配置和恢复边界见 [平台中控落地](../docs/dev/platform-coordinator.md)。外部集成仍不开放 coordination，普通 Chat 不整体改道。

本片交付 Harness 无关的作业门面：`retrieval-agent-job@1` 执行单页；显式装配的 `retrieval-agent-job@2` 支持有界多页与候选集合交付。二者串联需求确认、阶段规划、物理计划确认、检索、候选清单确认与产物查询。**前端继续使用原 `/chat` 和候选窗口；后端按项目显式启用新链路，默认配置不自动启用；当前本机8096的显式启用见[报告](../docs/architecture.md#13-运行装配与部署)。** 清洗、评测、训练、RHF 不在本片扩展范围。

## 记录与范围

`AgentJobRegistry` 注册后端安装的工作流，`AgentJobService` 提供统一 prepare/get/list/act/artifacts 门面。首个能力为 retrieval；其他工作流必须具备自身业务任务/授权/存储实现，注册名称不会赋予训练或清洗权限。

作业引用复用 `agentdraft_...` 阶段草稿 ID。正式 Task、批准、规划 run/attempt、检索计划、页面和候选清单仍是原记录；新增 `agent_job_links` 仅保存关联、编译意图和错误摘要，不增加平行任务或审批模型。准备阶段不创建正式任务/运行，不调用模型。

正式新自主模式默认拒绝确认；显式装配 `RetrievalAgentWindow` 后，限定项目可经原草稿与 `TaskService.create_task` 确认纯检索任务。仅支持默认维度、图片文本、`agent-requirements@2` 的空 processing、全人工节点和后端允许的来源/预算。默认单页；多页须装配 @2 且需求明确包含 paginate。不接受处理规则、打标、参考图、分组配额、历史去重或补数；旧草稿/旧任务不自动升级。新增本机用例从公开草稿及任务确认接口进入，不绕过准入；仍非真实依赖验收。

默认 Chat 使用连续对话和多词检索流程；项目的 `chat_entry` 默认为 `conversation`，只有显式 `candidate` 才由独立有界检索作业接管新消息。Agent API 的 enabled/授权与此界面策略独立，不因 Chat 入口调整而撤销外部 Agent 能力。

## 已确认词表执行模式

### 候选自动交接扩展

新 `confirmed-terms-execution@2` 在词表确认时明确批准首轮自动固定候选、按同一版本规则准备清洗配方并交给现有清洗后台。必须已装配清洗服务和派发器，检索/清洗节点为 auto，且存在已确认硬规则；打包发布仍为 manual。不自动开启补数，input_only 可只自动处理首轮。

新 @2 的首批输入以当前计划的首批查询/来源各一页为窗口；全部必要页已提交且无未知/待提交页后，才可固定候选。input_only 不要求先耗尽所有剩余游标；原始命中窗口仍受现有 manifest 容量和共享预算限制。保留后续游标不表示已经授权补数。旧 @1 和未委托任务继续执行其原有确认/分页规则。

`candidate_intake=auto` 是新装配默认值，候选窗口只展示，不等待额外确认；`manual` 为用户可选的“先审候选”，候选确认后仍自动进入预授权清洗。两种策略分别有 policy_digest，进入原正式任务确认摘要。旧 @1 原字段/摘要和候选、清洗人工节点保持不变，不升级历史任务。独立规划候选 @1/@2 作业的人工门禁不在本扩展中改变。

这里进入的是 `ConfirmedCleaningService` → 原清洗批准账本 → `CleaningDispatchCoordinator` → 已发布检测器/VLM/DJ 执行链，不是新接入独立清洗 Agent。规则、模型、来源、代次和预算变化仍拒绝旧批准，未知不计为通过。

首轮配方摘要、候选清单、预期版本及检测调用预留先保存到原 agent_job_links.intake，再以稳定键批准原清洗操作。回执或进程中断重放同一批准，不新建清洗；只读 get/artifacts 不推进交接。auto 候选已固定但清洗准备失败时，保留候选并返回实际错误码，不能仅因存在 manifest 显示整条交接已完成。

新清洗操作的指令由已确认原文及类别作用域确定性生成，冻结进原清洗配方，不以另一轮模型改写加入额外限制；已有配方按原 instructions 重放。`steps.available` 只表示存在可读回执或检查点，完成状态以各 run.status 及最终结果发布为准。

新 @2 的首轮检测预留参与同代次 max_vlm_calls 与后续补数统计；不混算检索次数或货币费用。不满足检测预算时不批准清洗。该扩展是新代码接线，部署及验证记录以 progress 为准。


默认 Chat 先用原对话/扩词流程生成完整词表，发起人确认时在原 query-spec@2 中保存 `retrieval_execution`。随后原 outbox 经公共作业框架进入 `confirmed-terms-retrieval@1`，复用原逐词计划、查询分支/组配额、页、共享预算和候选 manifest。没有第二次模型规划或词表确认；本模式不授权自主改词、超范围换源或预算扩大。查询/list/artifacts 仍不产生执行副作用。

`workflow_ref` 为该模式时，prepare 的 planning 仅含 expected_revision、expected_generation、expected_task_version。委托本身必须已由发起人在原任务确认中批准；prepare 不创建根 Task 或伪造批准，自动派发也只能消费该确认范围。作业引用按 Task/revision/generation 稳定，同一代次多次派发/重试不新建作业。原 `agentdraft_...` 路径格式继续使用，已确认执行模式不对应模型阶段草稿；不创建假的 model run/attempt。

旧 @1 执行顺序：原词表确认 → 原 Task/outbox → Agent queued → 原已授权多词工具 → waiting_confirmation(candidates) → 发起人确认原 manifest → completed/artifacts。新 @2 按上节预授权策略自动冻结输入并排队清洗，或显式先审候选。accepted_count 保持 null。未产生候选时可以结束检索并说明没有候选，不能生成空的已确认输入清单或冒充清洗通过。

`execution_scope` 在该模式中返回已确认的来源及调用/原始命中上限；query_terms 为原编译器的全部逻辑查询（含原种子/主词/辅助词）。预算先与作业意图在同一事务固定，再执行搜索。旧请求、旧确认、旧模型尝试不自动转换。修改词表沿原 Task 的新 revision/generation 派发，旧作业返回已失效并禁止旧回传；根任务取消同样先持久化代次栅栏。

新模式通过原候选确认入口或 OwnerClient.confirm_job 发布同一个原 manifest；不新增审批。已确认原产物优先于未完成的作业关联，重启/丢回包可只读查回，无需重搜。网络/页提交未知不自动重发，也不把未知计为空成功。执行端口当前装配 LOCAL_CLIP，多轮自主策略与新外部连接器仍需单独实现/验证。

## 生命周期（独立规划候选模式）

```text
已有确认任务 → prepare（原阶段草稿）
→ 发起人确认 planning → start（原规划尝试）
→ 后台编译 → 发起人确认 retrieval（原物理计划）
→ 后台搜索一页、授权映射/读取媒体、生成候选提案
→ 发起人确认 candidates（原 manifest）
→ completed，返回原 manifest 引用
```

首片全部保留人工节点。规划完成不授权物理搜索，检索确认不代表候选已发布；查询/list/artifacts 不调用模型、编码器或检索网关。后台仅处理已启动作业和已批准范围。

独立规划候选模式响应 `agent-job@1` 包含 job_ref、task_id、capability、workflow_ref、status、phase、原 revision/generation、当前 task_version、planning_review、confirmation、artifacts_ready 和 execution_scope。状态为 waiting_confirmation / ready / queued / running / completed / failed / unknown / cancelled；它是原业务记录的聚合视图，不覆盖 Task 状态。

`confirmation` 提供 checkpoint、proposal_digest、expected_task_version 和摘要；发起人提交这三个绑定及 action=confirm。`planning_review` 保留原阶段摘要供 start 使用；不能拿后续物理计划摘要代替。

`query_terms` 返回关联到已编译物理计划的真实策略查询；尚未编译时为空数组，不以需求原文冒充检索词。页面在执行检索前直接展示词表。`planning_progress` 提供已等待秒数、规划时限、已准入模型调用数与是否到达期限；不是模型完成度。超时未提交有效方案时明确告知未开始图片检索，保留原未知状态，不自动重试。读取这些字段不调用模型、编码器或检索。

batch_size 的后端上限为 1–100，还受需求数量和授权来源能力限制，在物理计划确认中展示。@1 的 max_pages 固定为 1，more_available 也不继续。@2 的页数上限为 2–10，缺少 paginate 授权仍只执行一页；页数、候选期望和原计划摘要共同进入作业的检索确认摘要，再通过原物理计划确认事务记录批准。运行按原游标逐页前进，每次推进最多一次搜索，仍受原共享 SearchBudget 限制。

@2 以真实物化的媒体身份跨页合并候选，保留重复命中的来源；相同身份但不同媒体版本内容冲突拒绝汇总。达到唯一候选期望、来源耗尽/深度限制或页数上限即停止，停止原因分别为 candidate_target/exhausted/depth_limit/page_limit。按整页检索，末页可能超出期望；检索卡展示 max_raw_hits=batch_size×max_pages 和 target_count，不承诺精确数量或清洗有效数。多轮策略反馈及自动节点仍待扩展。

候选集合不伪造合并搜索操作。@2 仍保存每页原提案/manifest，最终 candidates 确认摘要绑定有序页面提案、原批准计划、范围和停止原因，发起人一次确认。所有原 manifest 及作业关联在同一候选事务内提交，失败全部回滚。artifacts.items 返回各页原 manifest 引用，顶层 candidate_count 为跨页唯一候选数，不能累加各页 candidate_count 当成唯一数量；accepted_count 仍为 null。

## HTTP 与 SDK

基础路径 `/v1/retrieval/tasks/{task_id}/agent-jobs`。写入需 Idempotency-Key，路由不接受查询参数。

| 方法 | 后缀 | 行为 |
| --- | --- | --- |
| GET | 基础路径 | 最多 32 项当前授权作业，不推进执行 |
| POST | /prepare | capability=retrieval 与 planning 分配/版本字段，返回原阶段草稿引用 |
| GET | /{job_ref} | 当前状态及需要确认的摘要 |
| POST | /{job_ref}/confirm | 发起人确认 planning / retrieval / candidates |
| POST | /{job_ref}/start | 启动原已确认规划，重复不新建尝试 |
| POST | /{job_ref}/cancel | 取消根业务任务并提升 generation；远端停止状态未知 |
| GET | /{job_ref}/artifacts | 原候选 manifest 引用和安全元数据 |

输入复用阶段 API 的模型/预算/版本定义，见 [OpenAPI](agent-jobs.openapi.yaml)。请求不能指定上下文、运行器、解释器、媒体地址或索引向量。

外部集成沿用 [共享授权与根任务关联](shared-capabilities.md)，另需显式授予 agent.retrieval.read / agent.retrieval.prepare / agent.retrieval.start；根任务取消沿用 task.cancel。绑定仍校验具体任务、项目、revision/generation、需求摘要；合法业务确认提升 task_version 不使该根关联失效。不同集成/外部根引用的动作键隔离；缺少或复制根引用均拒绝。集成主体仅能访问本片 retrieval，不能借未来插件扩权。

`AgentJobClient` 可挂入外部 Agent 工具列表，没有确认方法。`OwnerClient.confirm_job` 只用于原发起人明确确认；换用集成凭证仍被服务端拒绝。SDK 延续 HTTPS、逐请求凭证解析、禁止重定向传凭证及未知写入不自动重发。

```python
from dsh_retrieval.shared_sdk import AgentClient, AgentJobClient, OwnerClient

# task_id/版本来自原发起人已经确认的任务。
modules = AgentClient(agent_transport, external_task_ref=external_root)
modules.bind(task_id, revision=revision, generation=generation,
             task_version=task_version, key=bind_key)
agent = AgentJobClient(agent_transport, external_task_ref=external_root)
job = agent.prepare(task_id, {
    'model_profile_ref': model_profile_ref, 'model_calls': 2,
    'expected_revision': revision, 'expected_generation': generation,
    'expected_task_version': task_version,
}, key=prepare_key)
# 以下调用属于发起人审阅后的确认流程，不挂入 Agent 工具列表。
owner = OwnerClient(owner_transport)
owner.confirm_job(task_id, job['job_ref'], job['confirmation'], key=planning_confirm_key)
agent.start(task_id, job['job_ref'], job['planning_review'], key=start_key)
# 后续读取 job；retrieval/candidates 分别经发起人确认。
# 完成后：agent.artifacts(task_id, job['job_ref'])
```

## 持久化、失败及取消

已确认词表执行模式完成当前窗口时，调用已安装recall端口将 `retrieval-recall@1` 分析保存在同一agent_job_links。job的 `recall_analysis` 仅返回recorded及来源scope/evidence_digest，或unavailable及错误码；记录可能属于先前task_version，不能直接当当前改词授权。实际读取/使用分析必须重新走当前授权的 `/retrieval-agent/recall`，新词仍经原词表确认。分析不可用不伪造零收益，也不撤销已确定的候选产物。

- prepare 重试复用原草稿，业务确认后仍可按原键找回；同键异体冲突。原节点重试不启动另一规划尝试或重复搜索。
- 原检索/候选确认增加仅后端可用的事务回调，批准/manifest 与关联同时提交；关联保存失败回滚原确认或发布。
- task_version 随计划确认提升后，仅该关联承接原规划记录的读取，原阶段 API 严格栅栏不放宽。任务改版/代次变化、计划替换、授权撤销仍拒绝当前发布与产物读取。
- 编码前保存意图，只有首次写入者编码。崩溃发生在计划发布、关联未写入时从原 handoff 来源恢复；没有持久计划的旧意图不自动重编。30 秒后可显示 unknown，这是展示阈值，不证明远端已停止。
- 原页面预留是搜索单派发栅栏。传输/提交中断保留待核对，重启不重发已预留页、不退款；已提交页可重新物化而不重搜。
- 候选确认前重新物化并比较提案；发布/查询复核项目、库和媒体权限。产物无内部地址/临时路径，accepted_count 为 null，verification 为 media_verified_rules_unchecked。
- cancel 作用于根 Task，复用共享接口相同栅栏：先原子提升 generation/清理发布资格，再停止本进程运行器。远端停止为 unknown，预算保留，重复不再提升代次，取消后不返回交付产物。
- 新表登记于原合法存储格式，任务/对话两条硬删除路径沿原阶段清理级联删除；不新增必装数据库，不静默重置未知/损坏格式。

## 装配与验证

```python
from dsh_retrieval.adapters.harness.composition import build_retrieval_agent_jobs
from dsh_retrieval.agent_jobs import AgentJobDispatcher

jobs = build_retrieval_agent_jobs(planning=planning, compiler=compiler,
    executor=executor, materializer=authorized_materializer,
    manifests=manifest_repository, batch_size=25)
worker = AgentJobDispatcher(jobs)
handler = create_handler(task_service, agent_jobs=jobs, ...)
# 关闭顺序：worker.close()，再 planning.close()。
```

首个存储适配与阶段/检索/候选同一 SQLite 文件；其他存储需实现同一原子业务关联端口。Dispatcher 从持久关联恢复，不依赖前端 GET 或内存队列。未注册/不兼容实现不回退。

启用有界多页时显式传入 `max_pages=3` 等 2–10 的值。默认仍为 1；构建多页门面同时保留 @1 解析，历史作业不会改变语义或页数。@2 使用 `agent-job-links@2` 保存原关联和有界页面提案/manifest 引用，不修改原 manifest 的模型、摘要或存储格式。重启装配不同页数不会改变既有作业分配；只影响后来准备的作业。降回仅 @1 装配时历史 @2 拒绝执行，需保留支持 @2 的运行实现来恢复，不能静默回退。

原窗口接线和项目启用方式见 [接入说明](../docs/dev/retrieval-agent-window.md)。用例见 [业务作业](../tests/test_agent_jobs.py)、[本机 HTTP/SDK](../tests/test_agent_jobs_http.py)、[公开任务确认与候选窗口](../tests/test_agent_retrieval_window.py)、[原发送脚本与确认卡](../tests/test_agent_retrieval_chat.py)。覆盖合成可解码 PNG、人工门禁、幂等、重启、并发、事务回滚、撤权、取消/旧回调、未知完成、删除级联及原窗口接线。真实身份、模型、来源和生产持久恢复仍需验证；结果和失败记录见 [progress.md](../docs/dev/progress.md)。
