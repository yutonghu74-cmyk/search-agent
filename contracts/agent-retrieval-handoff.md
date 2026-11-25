# 检索 Agent 提案到业务计划的桥接

本片接通后端 `AgentRetrievalService.prepare`：读取已完成阶段的持久化工具回执，解析当前授权的物理索引绑定，调用已发布的文本编码能力，生成并保存既有 `RetrievalPlan`。不接收模型向量、索引地址或过滤表达式；不根据 SDK 退出码或最终文字编译计划。

后续统一入口见 [Agent 作业服务](agent-jobs.md)：在已有确认任务上将本编译器、原确认、单页执行与候选产物串成 HTTP/SDK 作业。该入口不解除正式任务准入，不修改前端窗口，也不让规划确认代替业务确认。

## 接口与装配

- 业务端口：`AgentRetrievalHandoffStore.load/save`，领域快照为 `RetrievalHandoff`。服务不引用 DSH SDK 或原生会话格式，其他 Harness 通过相同阶段回执进入。
- 开发存储实现：`SQLiteAgentRetrievalHandoffs(journal=..., retrieval=..., authority=..., context_provider=...)`，两个账本必须位于同一文件。`authority(invocation)` 和 `context_provider(actor, task, role)` 必须为服务端授权的有界读取，不能执行网络作业或修改业务状态。
- 编码端口：既有 `encode_query(context, text, binding)`；能力为后端发布的 `TextQueryCapability`。调用 `AgentRetrievalService(handoffs=..., embeddings=..., capabilities=...).prepare(actor, handle, epoch=1, batch_size=...)`。
- 返回未批准的 `RetrievalPlan`。继续走既有 `retrieval.confirm`；阶段确认不代替业务计划确认。已新增下述显式后端页面执行器，自动作业 relay 和公开执行 HTTP 路由仍未接通。

## 已批准计划的显式页面执行

```python
from dsh_retrieval.agent_retrieval import AgentRetrievalExecutor

executor = AgentRetrievalExecutor(retrieval=retrieval_repository,
    gateway=authorized_retrieval_gateway, budget=shared_search_budget)
# approved 为原业务确认返回的计划，包含确认后递增的 task_version。
result = executor.execute_page(actor, approved.scope.task_id,
    plan_id=approved.request.plan_id, proposal_digest=approved.proposal_digest)
# result['operation'] 复用 RetrievalOperation；page 是原账本提交的检索证据。
```

预算端口必须提供现有 `attempt(scope, batch_size)` 语义。每次调用最多发一页，游标来自已提交前页；计划 ID/摘要须匹配当前已确认 Agent 来源计划，不接受提案阶段旧摘要。执行只依赖业务端口，不依赖 Harness/DSH/清洗 Agent。

原 `reserve_page` 的原子首次写入者是本执行器的派发栅栏：已提交页重放返回原结果，重复预留但未提交的页面返回 `pending_or_unknown`，不再调用检索网关或重复扣预算。跨进程并发也遵守同一账本栅栏。预算准入/搜索期间仍会复核原授权/版本，网络在事务外，发布继续校验游标/快照/旧 generation。

预算拒绝、网络/进程中断、无效页面或提交失败都保留未提交意图，不能转为零命中成功。初次错误原样抛出，后续查询/重试显示待核对；即使崩溃发生在发送前，也不自动猜测可以重发。实际连接器须提供专用核对/恢复后才可提交原结果，本片不提供通用强制重试接口。保留已预留预算，不声称精确费用或远端已停止。

返回 committed 仅表示检索页入账；不创建清洗作业、不写 accepted_count、不自动发布候选 manifest 或完成业务任务。后端服务仍需明确装配本执行器；公共阶段 API 保持 planning-only。

## 当前支持范围

图片、文本查询编码、`rewrite_query/switch_source`；查询方式允许已确认的 `text/vector`，此处 vector 指对策略中的文本进行编码，不能提交外部向量。后端重验完整 `IndexBinding`、证据和自主授权范围。`epoch` 受已确认的 `max_rounds` 及既有账本下一 epoch 共同约束；批量受发布能力及原接口上限约束。

分组扩词/配额、活动多分支计划、来源过滤、参考图、视频、翻页与重试策略均明确拒绝，不将它们转换成普通新查询。现有多分支计划要求逻辑查询和原始分支保持完整，后续需专门实现带来源归属的编译适配。

## 原子性、重试与撤销

编码前、保存事务内都检查正式任务、发起人、项目、revision/generation/task_version、当前阶段批准、输入摘要、工具提案正文和回执摘要。已完成但只有报告的阶段不构成检索策略。读取回执和编译不预留新的 Agent 模型调用，规划额度耗尽后仍可处理已生成的有效提案；这不表示已实现编码费用或检索资源总预算。

编码在数据库事务外进行。编码期间改版、撤权或输入变化拒绝发布。保存时复用既有检索计划校验和下一 epoch 校验；计划与 `agent_retrieval_handoffs` 来源记录在同一事务写入。计划 ID 绑定阶段、提案、epoch、批量与过滤模板；编码结果改变导致不可变计划冲突，不生成第二条计划。并发相同请求只产生一条计划，但准备重试仍可能再次调用编码器。

既有业务确认入口在递增版本前重新检查来源阶段是否仍为当前批准、尝试是否完成、回执是否未变。编译后取消或替换阶段，旧计划不能再确认。已完成业务确认的计划由业务任务/计划取消控制执行；规划阶段取消不会追溯撤销既有业务批准。读取和重试不代替确认。

新增来源表显式登记合法存储格式；任务硬删除和对话硬删除均级联清理。没有新增必装数据库，也不放宽未知表检查。

## 验证与未完成项

定向用例覆盖持久化策略到业务确认、页面预留/发布、幂等、并发、编码期间改版/撤权、确认前阶段替换/撤销/回执变化、跨用户隔离、错误编码器、动作边界和两条删除路径。

实际 rc.5 验证使用隔离运行目录和本机确定性模型：公共阶段 API 产生有效回执，重建服务后经本桥接保存物理计划，通过既有业务确认及空页面提交，模型请求保持 2 次。编码和检索页面使用确定性夹具；这不是内网检索、真实模型质量、真实持久化部署或完整回归验收。

正式新模式任务准入仍关闭。显式页面执行新增确定性网关的定向验证；自动检索作业服务、候选产物自动交付、完整策略反馈循环、筛选/标签产出及前端仍未由本片全部接通，不将本片标记为 T06 完成。清洗独立开发，评测/训练/RHF 按当前计划暂缓。
