# 检索 / 清洗双 Agent 契约（T01–T03）

后续实现补充：本文保留 T01–T03 原边界。当前统一作业与 [原窗口接线](../docs/dev/retrieval-agent-window.md)已支持后端项目显式启用后的 `agent-requirements@2` 纯检索任务准入；默认单页，显式多页作业和 paginate 授权支持最多 10 页。默认配置及处理/清洗模式仍拒绝。@2 空 processing、input_only 的检索来源由 autonomy.library_ids 独立声明，补数来源保持空数组，所有来源经后端授权复核。旧 @1 的下述字段/摘要约束不变。

## 状态与兼容

本切片实现需求草稿及阶段提案的纯校验边界，不提供运行/执行 HTTP API。角色为 retrieval 和 cleaning（包含打标/分析），现有对话应用服务与业务协调器衔接两者；没有第三个自主主 Agent。

`query-spec@2/@3` 增加可选 `agent_requirements`。缺省不注入空值/默认值，因此原有归一化结果、确认摘要和旧任务不因本次扩展改变；旧 CleaningRecipe / ApprovedCleaningJob 不改结构。新字段由 normalize_proposal 归一化，进入既有 Draft.proposal_json 与 proposal_digest，使用原草稿存储和幂等协议；不新增任务模型或存储格式。

现有 TaskService.create_task 对含此字段的草稿返回 `AGENT_EXECUTION_UNAVAILABLE`，不创建正式任务或批次。T04–T06 接通执行能力、能力预检与 UI 确认摘要后才能解除；不得单独删除门禁。旧客户端需升级到支持此扩展的归一化版本才能处理新草稿；旧任务不迁移为自主模式。

## agent_requirements@1

对象严格包含 `schema_version=agent-requirements@1`、`processing`、`autonomy`。

processing 为 1–32 条，每项严格包含：

| 字段 | 语义 |
| --- | --- |
| requirement_id | 唯一稳定标识 |
| purpose | filter / tag / rank / analyze |
| instruction / source_text | 有界处理描述与用户原话；source_text 必须出现在 raw_request |
| group_id | 已有分组或 null；不能引入隐含分组 |
| rule_ref | filter 引用已有硬规则/清洗条件，其余为 null |
| output_field / output_schema | filter 为 null；其余为输出字段及 schema |
| unknown_policy | filter 固定 hold_for_review；其他可为 allow_unknown，必填输出不可使用此值 |

output_schema 严格包含 version、type（boolean/number/text/enum）、values（仅 enum 非空，允许中文）、required（布尔）。rank 必须 number。同一分组输出字段唯一；filter 保持已有规则的适用范围。模型不得在这里提供算子路径或执行代码。

autonomy 严格包含：actions（rewrite_query/switch_source/paginate/retry 的子集）、library_ids（已批准补数来源的子集）、methods（text/vector/reference_image）、max_rounds/max_calls/max_seconds/max_no_progress_rounds（正整数，禁止 bool；无进展轮次不超过总轮次）。本字段只是待确认需求，不能充当访问凭证或批准。

## 阶段与提案

### 纯检索需求扩展：agent-requirements@2

为使检索与清洗独立开发/调用，显式新增 `schema_version=agent-requirements@2`，仍复用上述同一个需求模型/归一化和草稿存储，不复制公共模型。字段仍严格为 schema_version、processing、autonomy；processing 允许 0–32 条。空 processing 表示没有委托清洗/处理，不需要伪造筛选/标签占位项；此时 actions 和已批准 library_ids 均须非空，其余授权、预算与参数校验照旧。

`@1` 仍要求 1–32 条，已有归一化内容及确认摘要不升级；从 @1 改到 @2 是显式需求变更，摘要不同。空处理需求不能准备清洗阶段；不能据此把召回素材声明为清洗通过，原硬规则及业务计数不变。正式创建门禁同样覆盖 @2，当前仅可公开保存草稿；框架及检索后端测试使用可信确认夹具，不删除门禁。

### 共享阶段绑定

代码入口：processing_requirements.py、agent_contracts.py。

- AgentStageTask 复用 RetrievalScope，附带 operation、role、input_digest、requirements_digest、approval_digest、policy_version、model_binding_digest、budget_ref。所有字段进入 stage_digest。仅后端构造，不能接收模型提供的身份字段。
- RetrievalStrategyProposal 绑定 stage_digest，包含动作、IndexBinding、检索方式、查询、原因和证据引用；物理 binding 必须与当前授权兼容目录条目完全一致，不能只校验 library_id。
- ProcessingPlanProposal 绑定阶段和需求摘要，逐项将需求引用映射到现有 DetectionStep。ProcessingCapability 为后端发布记录，声明媒体类型、用途和输出类型；每项必须被恰好覆盖一次，未发布或用途不兼容拒绝。
- 不引入第二套可执行 CleaningRecipe。处理提案不直接成为 ApprovedCleaningJob；未来编译器必须再做能力、语义与确认校验。tag/rank/analyze 暂不能下发到旧过滤执行器。
- StageFeedback 是业务服务生成的统计快照；pending/failed/unknown 的有效新增和缺口必须为 null，不能冒充零收益。AgentStageReport 只含结论、建议和证据引用，无权写计数；规则变更建议必须为 needs_confirmation。

调用 `validate_agent_proposal`：重验现有 validate_scope（身份、任务、项目、revision、generation、task_version、状态），比较后端 current 已批准阶段记录，核对正式任务内冻结需求及摘要，检查剩余调用预算，再做角色专属来源/能力覆盖检查。

调用 `validate_agent_report`：重验当前任务和批准绑定，校验证据范围；报告不消耗执行额度，预算耗尽仍可报告停止。证据必须由业务层解析为当前任务、当前授权范围内的引用，不能直接接收 Agent 自报的可用证据列表。

这些函数均为无副作用 preflight，不是持久批准、预算预留或事务门禁。current、授权 binding、capabilities、剩余额度和证据集合必须从后端权威读取；T05 在执行/发布事务内重新校验并 CAS/预留，不能拿一次校验结果长期执行。T03 不声称已验证跨进程并发预留或真实权限撤销竞争。

## 确认门禁盘点

| 现有门禁 | 双 Agent 处理 |
| --- | --- |
| TaskService.save_draft / create_task | 复用草稿和发起人确认；新增 Agent 草稿执行前阻断 |
| node_modes 和 refill_policy.progression | 原手动/自动范围保留，不因角色上线改变 |
| 检索计划/候选清单确认及物理绑定 | 保留原授权、幂等和版本栅栏；策略只是提案 |
| ConfirmedCleaningService.prepare/confirm | 保留已发布能力、清单、配方与批准摘要绑定 |
| ConfirmedRefillService.confirm_next / 既有自动补数路径 | 保留位置预留、缺口和批准范围，不允许两个 Agent 递归互调 |
| 打包与交付 | 保留原确认和结果快照语义；不以报告状态标记任务完成 |

跨批次授权、费用预留、标签结果持久化和 DSH 工具接入分别留待后续切片。无新生产数据库要求，不修改 vendored DJ。


## Harness 可替换性补充（设计约束，待 T04 实现）

领域契约不得引用 DSH SDK、原生 session/event/tool 名称。新增 HarnessInvocation 引用既有 stage_digest，分别保存 runtime_release、attempt 和本地会话映射，AgentStageTask 不复制或承载供应商配置。model_binding_digest 是业务/模型语义绑定；旧 DSH 的混合 config_digest 不重新解释，后续以版本化迁移保持旧批准可审计。

角色定义与逻辑工具一次定义；MCP、SDK 函数和 RPC 都调用同一 ToolBroker，复用 T03 检查。审批、预算、幂等及有效计数依旧由业务服务保证，不委托给 Harness 原生 Team/批准/会话服务。仅拿到 SDK 最终文本或成功退出不算有效业务提案。

新增 Harness 必须先声明并验证必需能力，特别是工具限制、截止时间、取消/未知状态核对与包含内部模型调用的预算控制。不支持原生恢复的引擎可从业务检查点恢复；不允许伪造跨引擎会话恢复。升级以固定发布版本并行验证，按角色路由，新运行固定版本；无自动 latest 或静默 fallback。

T04a–T04b 已新增该端口与共享规划工具，旧/新DSH已通过真实SDK和实际rc.5 CLI基础兼容验证，见 [运行契约](harness-runtime.md)。持久化调度、生产预算和恢复能力准入尚未完成，Harness 可替换目标未作整体验收。具体接口、审视发现及 T04a–T04f 准入见 [Harness 可替换性审视](harness-runtime.md)。
