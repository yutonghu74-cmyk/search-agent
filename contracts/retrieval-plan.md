# 检索计划账本契约（待集成）

状态：A 侧契约与纯校验代码，本提交作为后续开发基线；未实现仓库/HTTP，不是已上线能力。旧接口未变，真实适配器能力仍须另行冻结。

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
- 原接口未改签名，新增账本端口没有默认实现。现有 SQLite 快照、HTTP 和 CandidateIntake 不会自动支持该契约。正式接入前必须补原子确认/回传/恢复测试，发布新基线后才能由其他角色实现。
- 下一切片：core 实现与任务共事务的计划账本（先本地 SQLite），retrieval 从已批准记录执行，A 装配空召回与非空候选确认。真实 ClickHouse/Milvus 和清洗联测另验收。
