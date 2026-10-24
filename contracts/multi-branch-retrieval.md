# 多分支批准及调度契约

本切片发布不可变 RetrievalPlanSet、纯校验函数和 RetrievalPlanSetRepository 协议，不实现账本或执行器。复用 PreparedQuery 与 RetrievalPlan；不得连续确认单计划来模拟整组批准。

集合由 scope、epoch、members 组成。members 为 1–64 个 PreparedQuery 的 tuple；这是本地安全上限，不是规格的固定业务分支配额。所有成员必须同一 scope/epoch、同一当前逻辑预览摘要。按当前确认方案重新编译并核对 branch_id/query_fingerprint，至少保留原文分支。同一查询指纹与完整物理绑定组合只能出现一次，plan_id 不重复，同一 binding_id 不允许混合索引/编码器版本。跨库可重复同一逻辑查询，未来融合仍需按逻辑指纹防止重复加分。

集合摘要绑定全部物理请求、来源、逻辑摘要与成员顺序。确认输入示意：`{proposal_digest: "sha256:…", expected_task_version: 1, action: "confirm", key: "request-key"}`。只允许当前发起人，任务必须处于允许状态且作用域未变化；纯校验通过不等于批准成功。

core 实现要求：put_proposal 只接收后端准备结果，事务内复验项目和所有库权限。confirm 在共享任务事务中完成 CAS、整组摘要校验、全成员授权、一次 task_version 增长、所有成员 scope 重绑定、批准指针与 outbox、幂等记录。相同 proposal 多 key 仅一次效果，同 key 换载荷冲突；旧确认重放不能重新激活已替换集合。替换时 epoch 递增，不逐成员批准。

调度请求示意：`{approved_set, plan_id, request, key}`，approved_set 只能来自可信账本。reserve_page 必须验证当前集合、成员、请求和来源游标，再原子预留；集合级在途预留上限 100，超限拒绝。各成员独立游标链，不可借用其他成员游标。请求中的向量/过滤模板/分页大小不得偏离批准成员。网络调用在事务外进行。

commit_page 必须验证预留 operation 归属、集合摘要/epoch、成员 plan_id、task/revision/generation 与请求，并在同一事务提交页面、幂等和 outbox。使用已有 validate_page 校验内容、快照和游标。重复一致回传返回 replay，变化内容拒绝；旧集合、旧 generation 的晚到结果不得覆盖新结果。检索耗尽不等于合格数达标，不改 accepted_count。

兼容性：新增独立协议，不改变现有 RetrievalPlanRepository 或 SQLite 单计划表；迁移、事务实现、恢复验证和多分支 worker 装配必须由 core 后续工作包完成。接口不接受用户伪造的授权对象。现有单计划执行路径维持原行为，不能把新集合传入旧账本。

验收仍需后续实现证明：整组中途失败回滚、撤权、幂等并发、恢复后当前指针一致、跨分支游标拒绝、替换期间晚到结果拒绝。当前测试仅证明纯契约边界，不代表这些事务验收通过。
# R06 恢复读取及有界协调器补充

`get_progress(actor, approved)` 在同一授权读取事务返回每个成员的 `PlanSetMemberProgress(plan_id, request, operation_id, page, committed_pages)`，顺序与批准成员一致。无请求时为初始 request/op=None/page=None/count=0；未完成预留为相应 op/request/page=None；否则为最新已提交页及其 request/op。计数仅为分页数，不是有效素材数。读取前后复验发起人、项目、所有库权限、任务 scope、当前集合，校验整个成员页链；重复游标、孤立预留、混合成员、损坏记录拒绝，不从头重置。

`RetrievalCoordinator` 包装 put_proposal/confirm，但确认只由调用者明确传入 action/key/version/digest，不自动批准或派发。`tick(actor, task_id, max_requests=1)` 只消费当前已批准集合，读取恢复点、确定性键预留、再次读取是否已完成、调用 gateway、提交回传，再读取当前进度。单 tick 最多 1–64 次外部请求，每成员最多一次；优先未完成预留，再按已完成分页数公平推进。重建协调器不持有业务进度，全部恢复点来自账本。

可重试依赖错误（显式 retryable 且 429/502/503/504）保留原 operation，报告 retry_pending；下一次显式 tick 复用该请求/预留，不静默开新页。权限、版本、游标和结果错误直接失败。已提交页跳过，终止页不再派发；分页停止不代表任务完成、目标数量满足或素材合格。检索不更新 accepted_count，不自动清洗或补数。

本阶段为受控单调度者的显式有界执行入口，不是完整生产调度器。没有跨进程租约、持久尝试/费用计数、定时退避或自动执行策略批准；未知网络结果可能被重发，同一逻辑页业务提交幂等但外部调用不保证恰好一次。启用自动循环和多调度者前必须接已批准 ExecutionRules、持久 attempt/预算及租约（R23）。真实 gateway 和分页能力仍分别验收。恢复接口缺失时构造协调器即报 RECOVERY_NOT_CONFIGURED，不读 SQLite 私有状态绕过权限。

## R06 只读 HTTP 进度投影

`GET /v1/retrieval/tasks/{task_id}/retrieval-progress` 只从当前已批准整组账本读取。先认证任务发起人和项目，再由账本在同一读取事务中复验当前 task/revision/generation/plan 和所有成员库的现行搜索授权。无批准时返回 `status=unapproved`；已批准时返回摘要、epoch、任务版本及每成员 `plan_id/branch_id/library_id/binding_id/index_version/committed_pages/status/stop_reason`。状态仅为 `not_started/pending/more_available/stopped`；`pending` 表示已预留但未提交，不能区分正在请求与需要重试。`pagination_stopped` 只指已批准检索分页停止，不等于有效素材数达标或任务完成。响应不含向量、原始命中、游标、内部 operation ID 或媒体地址。

未装配账本返回 503，非发起人 404，撤权 403，旧 generation/版本冲突 409；不允许查询参数。现有本地服务默认关闭，可用 `--plan-set-progress` 显式开启，必须同时指定独立 SQLite 任务账本、受信本地目录源与 `--demo-project`，并沿用单一 `demo/default` 项目授权。此入口仅供本地试验，不代替 W3、生产授权或真实索引连接。没有 POST 派发接口；HTTP 请求重放还缺持久请求级幂等及执行预算，不能把 GET 状态投影当成可安全自动执行的依据。
