# 检索计划账本契约

状态：公共契约及本地 SQLite 账本切片已实现；HTTP/业务清洗装配未实现，不是已上线能力。旧接口未变，真实适配器能力仍须另行冻结。

## 范围与依赖

- 依据 v1.9 6.5–6.7、11.1.1、11.2.2、13.5、14.1.1。
- 公共模型在 src/dsh_retrieval/retrieval_contracts.py；账本端口在 ports.py 的 RetrievalPlanRepository。复用已有 SearchRequest/SearchPage，不复制另一套检索模型。
- RetrievalScope 绑定 tenant、owner、project、task、revision、generation、task_version、需求摘要；RetrievalPlan 再固定 plan_id/version、epoch、物理绑定/索引/编码器、向量、过滤模板与单页上限。
- scope 由可信任务记录构造；物理绑定和过滤模板来自服务端授权配置。客户端不提交任意 SearchRequest/SQL，不能自行构造 current 记录作为批准凭据。

## 确认与执行顺序

1. 服务端保存提案：不执行搜索、不提升 epoch、不创建清洗作业。提案展示完整范围与 proposal_digest，后端保存原始计划。
2. 仅发起人显式确认。仓库在与任务共用的短事务中重新鉴权，检查 expected_task_version/摘要及计划基线，递增 task_version，保存绑定到新 task_version 的批准计划及当前指针、幂等记录。提案原摘要保留用于确认重放；执行计划重新计算摘要，不可混用两者。
3. 同键同正文重放原响应，同键异正文冲突；不同键确认同一提案也仅一次生效。新计划生效后，重放旧确认不重新激活旧计划。
4. reserve_page 读取权威 current，验证 scope/epoch、批准查询及前驱游标，预留 operation_id。网络检索在事务外执行，不持有任务锁。
5. commit_page 根据已经预约的 operation_id 取回原请求，重新鉴权并检查当前 task/revision/generation/plan/epoch。页内容、命中、下页游标、原始证据及 outbox 在同一事务提交。重复 operation 的相同结果只记一次；变更结果冲突。
6. validate_* 都是无副作用前置检查，不持久化、不获取项目/素材权限、不做事务，不得当作生产批准接口。跨系统权限竞争仍需部署方案和验收。

## 版本与 epoch

计划替换由仓库分配严格递增 epoch（每任务单调）；不让模型选择。变更查询/索引/过滤/排序等需新计划确认。旧回传不得发布。保持同一快照翻页不提升 epoch；失效快照不自动回第一页。新 epoch 的历史去重承接不在此切片实现。

## 页结果与空召回

| 情况 | 本契约行为 |
| --- | --- |
| snapshot + exhausted + 空 hits | 合法，记录本查询空结果；不创建清洗作业，不标记业务完成 |
| more_available | 必须有非空 next_cursor，不得重复当前或已见游标 |
| depth_limit | 无 next_cursor，只表示连接器深度限制，不是全库无数据 |
| 后续页 | 必须携带已登记前驱，保持一致性模式与 snapshot_ref |
| live + exhausted | 当前旧 SearchPage 表达能力不足，拒绝；不能假装全库耗尽 |
| 授权/依赖故障 | 错误直接传播，不转换为空结果 |
| 非匹配绑定、超单页上限、非有限分数 | INVALID_SEARCH_PAGE，拒绝发布 |

snapshot exhausted 也仅指批准查询及连接器可访问集合，不证明全库无合格素材。候选清单与媒体映射仍须独立授权，检索 hits 不等于 pass 或 accepted_count。

## 错误与兼容

- NOT_FOUND：非发起人/跨租户任务；REVISION_CONFLICT：旧任务/版本/代次/需求；INVALID_STATE_TRANSITION：暂停、取消、终态、归档。
- PLAN_DIGEST_MISMATCH / CONFIRMATION_REQUIRED：摘要失配或未明确确认。
- PLAN_NOT_CURRENT / PLAN_REQUEST_MISMATCH：未批准/旧 epoch 或改变批准请求。
- INVALID_SEARCH_PAGE：连接器输出不可信；不是业务内容 fail。
- 原接口未改签名；SQLiteRetrievalPlanRepository 为显式装配的本地实现，路径 src/dsh_retrieval/adapters/task_storage/retrieval.py，构造时必须注入项目/库权限服务。HTTP 和 CandidateIntake 不会自动接入。
- 原子确认/回传/恢复的本地证据见 tests/core/test_retrieval_storage.py 与 docs/dev/handoffs/core.md。任务表和检索表共库同事务；本地事件尚无发送器，不代表生产 outbox 完成。
- 下述授权读取接口已由本地 SQLite 适配器实现并验证；下一切片冻结候选清单发布/媒体物化接口并装配确认流程。真实 ClickHouse/Milvus 和清洗联测另验收。

## 授权恢复读取（本次新增契约）

- `get_operation(actor, task_id, operation_id)` 返回不可变 RetrievalOperation，字段为 operation_id、plan、request、page，status 派生为 reserved/committed。page=None 表示已预约未提交，不是失败，也不是零结果；已提交空页的 status 仍为 committed。
- `list_pending(actor, task_id)` 只列当前批准计划未提交的作业，operation_id 排序，最多 100；超限返回 INPUT_LIMIT，不截断。当前没有批准计划时返回空元组；权限/存储故障不得转换为空列表。
- 这两个方法是可信后端接口，不是新 HTTP 路由。每次重新校验所有人/项目/库和当前 task/revision/generation/plan/epoch；暂停、取消、终结、旧计划拒绝。跨 task/owner/tenant ID 按 NOT_FOUND 隐藏。历史浏览另设契约，不能靠恢复入口读取旧可执行计划。
- 返回只读快照，不修改 task_version、不创建事件、不派发、不获取 lease。两个恢复进程可能读到同一个作业；正式执行前还须预约/重查，外部调用费用与业务幂等分别处理。实际自动接管和 exactly-once 执行不在承诺内。
- 原始 hits/score 不授予媒体访问权；下游必须再通过 MediaRepository/MediaAccessGateway 当前授权解析、读取。候选发布和清洗确认不由读取接口自动完成。
- 兼容性：新增读取方法及返回模型；已有写入签名/存储格式不变。旧实现不具有这两个新方法，装配必须显式升级，不能退回直接读私有字典。
