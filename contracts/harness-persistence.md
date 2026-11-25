# Harness 持久化规划与模型预算（T05 首片）

## 实现范围

在现有业务 SQLite 同库新增阶段批准、队列、租约、模型预算、提案回执及事件，不复制 Task/RetrievalScope，不以 DSH session 当业务状态。DurableHarnessRunner 通过 HarnessJournal 端口使用存储，当前 SQLiteHarnessJournal 是开发持久化适配器；生产数据库仍经接口选择，PostgreSQL 非必装。

这一片接通的是检索/清洗 Agent 的持久规划，不直接执行检索或 DJ 任务。ToolBroker 和 journal 均拒绝 execute 工具，TaskService 的 AGENT_EXECUTION_UNAVAILABLE 门禁继续保留。公共 HTTP 阶段确认入口、全链路业务派发及标签执行/交付尚未接通，不把内部组件测试记作正式产品上线。

## 存储与权限

SQLiteHarnessJournal(repository, projects) 强制使用 SQLiteTaskRepository.path。每次批准、入队、claim、预算准入、回执发布和恢复都在 BEGIN IMMEDIATE 内读取同库业务任务，核对当前项目权限、revision/generation/task_version、需求摘要和当前阶段 head。阶段批准还要求发起人 approve 权限、action=confirm 和所展示 stage_digest 完全一致。

同 operation 的不同阶段正文冲突，重复确认旧阶段不能恢复已被替换的 head。身份始终来自后端 Actor 与冻结任务；模型不能传入新租户、预算或批准凭证。普通读取也重新鉴权；清理可以在任务 generation 改变后按所有者权限执行，但不能发布旧结果。

新增表：harness_journal_meta（format=1）、harness_stages、harness_stage_heads、harness_model_budgets、harness_attempts、harness_tool_receipts、harness_attempt_tools、harness_run_events。旧 SQLite 仓库显式识别这些表，重启不会误判为陌生数据库。既有硬删除任务/对话事务级联清理相关阶段、队列、回执、事件和预算，不留下可恢复的已删除上下文。

无效格式拒绝读取，SQL 错误返回 STORAGE_UNAVAILABLE，不回退内存或重建账本。表结构添加仅由组件初始化在所指定的业务库完成；本次测试只操作隔离副本/临时账本，未连接现场库。

## 派发、幂等及恢复

1. 后端准备现有 AgentStageTask，发起人明确确认后 journal.approve 保存批准。
2. DurableHarnessRunner.enqueue 保存完整应用运行信封和模型调用分配额，固定角色/模型/运行发布。重复 run/attempt 及同正文返回同句柄，正文或分配额变化冲突；修改配置不重绑定既有尝试。
3. 重启后的 worker 可以 pending(actor, project) 发现待运行队列；仅返回本人、指定项目且批准仍有效的工作，按入队时间排序。过期阶段队列会取消，不启动旧任务。
4. dispatch 先 probe 必需能力，再事务 claim；同阶段只允许一个有效运行尝试，不同进程/连接不会重复 claim。claim 同时扣减可分配模型次数并保存随机租约令牌，不仅依赖进程内锁。
5. 后台心跳续租并保存终态，不依赖前端查询。工具和模型准入均检查令牌、租约及 deadline，改版/暂停/取消/新阶段 head 立即阻断旧写入。
6. 纯规划工具在同一事务内校验并保存正文/回执。缓存命中仍核验当前权限。跨尝试相同提案可复用回执，同键异体冲突；本模型仅允许纯、短时读取/校验处理函数，禁止在数据库写锁内执行网络、模型或业务副作用。
7. journal 保存最终提案/报告和安全事件，重建 Runner 可直接读取，不再调用 Harness。

recover 只处理租约已经过期的运行尝试：已提交有效回执则恢复完成；否则标为 unknown，撤销旧令牌。原尝试不自动重排或再次调用模型。需要继续时由协调器显式创建新 attempt，并重新检查剩余预算与冻结绑定。当前原生 DSH 会话不跨 Harness 迁移；这里恢复的是业务规划回执与队列。

已过期的 running 尝试必须先 recover，不能通过启动第二个 run_id 绕过同阶段并发栅栏。外部请求完成不明时保持保守预留；没有自动退费、重新解释原话或盲目重试。

## 模型调用预算

现阶段以已确认 agent_requirements.autonomy.max_calls 作为任务 revision/generation 的模型调用总上限。所有角色、operation 和 budget_ref 共享这一代次池，换角色、换库或改引用不能重置额度。每次 claim 为尝试预留 model_calls，必须是正整数且不超过池剩余值。

模型调用与提案校验是不同事项：纯读取/提案工具不消耗模型次数；T03 原有动作准入仍由业务 authority 提供，不能将模型已使用最后一次额度解释为已生成提案不可提交。费用、token、检索/检测执行次数与跨平台全局预算尚未合并，不报告不存在的精确费用。

运行时逐个请求经过 admit_model_call(sequence)：检查当前租约/权限/阶段，再将 admitted 递增落盘，最后才转发。相同或乱序 sequence 拒绝，不能复用一个成功准入转发多次。内部重试也重新经过准入，超出分配额时请求不出本机网关。

所有尝试预留均保守保留，即使正常完成只用了其中一部分也不自动返还。本片优先保证未知故障下不超分配；不宣称实现了按实际账单结算或安全退还未用额度。admitted 表示转发前已准入，网络是否真正处理可能未知，不等于成功请求数或已计费次数。

## 实际 DSH rc.5 接线

持久模式要求 adapter 声明 model_call_admission，并由进程协议在启动信封中明确 model_budget_required。DSH worker 将模型地址指向本机每尝试独立的 ModelGateway；网关通过父进程/同库 journal 准入后，才请求后端配置的真实模型地址。

当前处理 OpenAI-compatible chat-completions 路由，未知路径、重定向均不转发；没有直连 fallback。SDK 模型重试仍经该地址，不因第一次请求失败获得免费额度。新 Harness 必须提供等效的出口控制能力才能进入持久模式，不能只声明一个字符串绕过准入验证。

在此持久模式中，业务提案/报告已经通过工具落盘后，DSH 在对应成功工具事件结束规划，不再额外请求模型生成结束文字。这是规划工具回执的完成条件，不是业务任务完成。普通 T04 内存测试模式保持原行为，不宣称有持久预算保证。

ModelGateway 不记录请求体、密钥或原始模型异常；运行信封仍不含凭据。模型地址由后端不可变配置解析。生产真实模型的网络、TLS、权限撤销和实际费用验收仍需另做。

## 使用入口

```python
journal = SQLiteHarnessJournal(task_repository, project_access)
runner = build_planning_runner(
    config,
    authority=read_current_stage_authority,
    model_environment=resolve_immutable_model_environment,
    model_binding=resolve_immutable_model_binding,
    journal=journal,
    actor=backend_actor,
)
# 此前必须完成明确的阶段确认；不能将模型提案自动当成 journal.approve 的参数。
handle = runner.enqueue(stage, run_id=run_id, attempt_id=attempt_id,
    model_profile_ref=model_ref, task_kind='plan', deadline=deadline,
    context_json=authorized_snapshot, model_calls=allocation)
runner.dispatch(handle)
# 查询会返回持久状态与有界事件页，不依赖原来的进程句柄。
view = runner.poll(handle)
```

只传 journal 或只传 actor 会拒绝装配。普通入口不传两者时仍是 T04 内存规划组件；正式业务路径必须显式选用持久装配，不做自动降级。调用方负责从已批准、不可变的模型发布解析 model_ref；enqueue 和 dispatch 均核对其语义摘要。

## 定向验收

- tests/test_harness_journal.py：同库确认/预算原子性、16个并发 claim 只成功一次、排队发现隔离、跨角色预算不重置、过期租约/旧 generation、撤权竞争回滚、同键异体、回执后崩溃恢复、独立 Python 进程读取、后台终态落盘和任务/对话删除级联。
- tests/test_harness_durable_native.py：实际 rc.5 + 本机确定性模型，分配2次则完成有效提案；分配1次则下一请求被拦截，模型接口实际只收到1次。重建 Runner 读取有效回执或失败状态，不再次发请求。
- 本轮相关 Harness/契约/草稿/SQLite/事务/旧DSH组142项定向全部通过，无跳过。不是完整回归、真实模型质量评估或生产试点验收。

T05 的持久规划基础已接通；正式阶段确认API、真实检索/DJ执行派发、标签生产与交付仍待后续切片。跨实例生产存储验证、实际费用账单核对及更细的资源预算不在本片验收结论内。


## 公共阶段 API 进展

已通过AgentPlanningService/StageReviewStore接入公共审阅草稿、确认、启动、查询/列表及取消，批准和草稿状态、入队和run标识分别同事务提交，实际rc.5 HTTP闭环已验证。参见 [阶段API契约](agent-stage-api.md)。这仍面向已经确认agent_requirements的正式任务；新的正式任务准入、真实检索/DJ执行派发和标签交付保持待办，不把规划确认替代业务执行确认。
