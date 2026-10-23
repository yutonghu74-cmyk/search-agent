# 多分支批准及调度契约

本切片发布不可变 RetrievalPlanSet、纯校验函数和 RetrievalPlanSetRepository 协议，不实现账本或执行器。复用 PreparedQuery 与 RetrievalPlan；不得连续确认单计划来模拟整组批准。

集合由 scope、epoch、members 组成。members 为 1–64 个 PreparedQuery 的 tuple；这是本地安全上限，不是规格的固定业务分支配额。所有成员必须同一 scope/epoch、同一当前逻辑预览摘要。按当前确认方案重新编译并核对 branch_id/query_fingerprint，至少保留原文分支。同一查询指纹与完整物理绑定组合只能出现一次，plan_id 不重复，同一 binding_id 不允许混合索引/编码器版本。跨库可重复同一逻辑查询，未来融合仍需按逻辑指纹防止重复加分。

集合摘要绑定全部物理请求、来源、逻辑摘要与成员顺序。确认输入示意：`{proposal_digest: "sha256:…", expected_task_version: 1, action: "confirm", key: "request-key"}`。只允许当前发起人，任务必须处于允许状态且作用域未变化；纯校验通过不等于批准成功。

core 实现要求：put_proposal 只接收后端准备结果，事务内复验项目和所有库权限。confirm 在共享任务事务中完成 CAS、整组摘要校验、全成员授权、一次 task_version 增长、所有成员 scope 重绑定、批准指针与 outbox、幂等记录。相同 proposal 多 key 仅一次效果，同 key 换载荷冲突；旧确认重放不能重新激活已替换集合。替换时 epoch 递增，不逐成员批准。

调度请求示意：`{approved_set, plan_id, request, key}`，approved_set 只能来自可信账本。reserve_page 必须验证当前集合、成员、请求和来源游标，再原子预留；集合级在途预留上限 100，超限拒绝。各成员独立游标链，不可借用其他成员游标。请求中的向量/过滤模板/分页大小不得偏离批准成员。网络调用在事务外进行。

commit_page 必须验证预留 operation 归属、集合摘要/epoch、成员 plan_id、task/revision/generation 与请求，并在同一事务提交页面、幂等和 outbox。使用已有 validate_page 校验内容、快照和游标。重复一致回传返回 replay，变化内容拒绝；旧集合、旧 generation 的晚到结果不得覆盖新结果。检索耗尽不等于合格数达标，不改 accepted_count。

兼容性：新增独立协议，不改变现有 RetrievalPlanRepository 或 SQLite 单计划表；迁移、事务实现、恢复验证和多分支 worker 装配必须由 core 后续工作包完成。接口不接受用户伪造的授权对象。现有单计划执行路径维持原行为，不能把新集合传入旧账本。

验收仍需后续实现证明：整组中途失败回滚、撤权、幂等并发、恢复后当前指针一致、跨分支游标拒绝、替换期间晚到结果拒绝。当前测试仅证明纯契约边界，不代表这些事务验收通过。
