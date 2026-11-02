# 补数决策门禁（当前实现边界）

`decide_refill` 是无副作用的业务层决策原语，不负责搜索派发、授权或结果发布。输入必须来自当前确认版本：目标、已批准库范围、推进模式、节点门禁、已验证的候选查询位置和已发出指纹。查询位置指纹须涵盖物理索引版本、查询、参数及游标/深度；协调器未来须在派发前原子登记指纹，并按 task/revision/generation 验证回传。

决策先判断目标达成，再判断 `input_only` 范围结束；`maintain_target` 仅能选择批准库中的未发出位置。预算用尽或人工门禁不能自动派发。所有可行位置尝试完毕后，按有界无进展证据区分 `NO_PROGRESS` 与 `SEARCH_STRATEGIES_EXHAUSTED`。未通过、未知、重复及撤权素材均不计入有效数。

`INPUT_SCOPE_COMPLETE` 表示已确认的固定输入处理完且仍有缺口，可以部分交付，**不代表检索语料或策略已耗尽**。旧快照仍保留原 `SEARCH_STRATEGIES_EXHAUSTED` 原因及摘要供兼容读取；新快照使用新原因。默认正式任务仍使用 `input_only/manual`；只有明确确认 `maintain_target/auto` 才会自动派发下一批准检索位置。若还同时明确确认检索、清洗、补数三个节点均为 `auto`，LOCAL_CLIP 补检索完成后才可自动固定增量清单并批准 VLM；仅补数节点为 `auto` 的任务仍保留候选和清洗人工门禁。

## 持久化预留边界

正式草稿可确认 `maintain_target/manual`，也可在补数节点为 `auto` 且两处模式一致时确认 `maintain_target/auto`。扩词和打包仍保持人工；检索与清洗只允许成组切换为 `auto`，并要求补数同时为 `auto`，避免只自动一半的候选/VLM 链路。确认范围必须列出非空、唯一的库 ID；预留时再次校验当前任务的 revision、generation、task version、运行状态、项目权限和库搜索权限。`build_refill_position` 将库、物理索引版本、查询、参数以及游标或深度规范化为查询位置指纹。

`SQLiteRefillReservationRepository.reserve` 在同一 SQLite 事务中先登记查询位置再返回，按 task/revision/generation 分配 iteration。同一位置重试返回原 iteration，不重复登记；重启后可恢复。旧 generation、固定输入任务、范围外库、撤权和终态运行均拒绝。该账本本身不创建检索作业、不读取候选、不触发 VLM；自动模式仍由协调器消费持久事件、验证同一 execution scope 后派发。

预留事务同时写入 `refill_dispatch_outbox`。`RefillDispatchCoordinator` 只消费已提交事件，投递前再次核验当前 revision/generation/task version、运行状态、策略摘要和库权限；旧 scope 记为 `superseded`，撤权或可重试依赖错误保持 `pending`。处理器以 reservation ID 作为稳定幂等键；若进程在外部调用成功后、outbox 标记前崩溃，恢复时允许用同一键重投。上一切片已存在但没有 outbox 的预留会在打开账本时回填。当前处理器仍为注入端口，尚未装配真实检索或候选流水线。

查询位置不能只保存不可逆指纹。新预留同时保存规范化 JSON 载荷，字段固定为库、物理索引版本、查询、参数和游标/深度；读取和派发时重新计算指纹并拒绝任何漂移。旧预留若没有该载荷，不得猜测或按任意查询派发，须标记 superseded 后重新规划。

`ConfirmedRefillSearchHandler` 是 outbox 与搜索执行器之间的失败关闭桥接。它必须从服务端当前批准计划解析完整位置集合，只有指纹和规范化载荷都与预留完全一致时才调用 `start_refill`。执行器回传必须绑定同一 reservation/revision/generation/iteration，否则事件保持 pending 并报告 `REFILL_DISPATCH_MISMATCH`。

当前已有两种实现：`LocalCLIPRefillAdapter` 从当前批准的 plan set 解析下一页，`CombinedCLIPRefillAdapter` 从当前组合 CLIP 作业解析下一游标请求。两者每次只执行预留所绑定的一个 plan/cursor；执行前后均核对 revision、generation、批准计划和查询位置，不能借补数切换分支、库、索引或查询。清洗推进造成 task version 增长时，只允许在同一 revision/generation、同一批准计划且任务仍处于可运行状态下续页；普通首搜接口仍保持原版本校验，不接受该放宽。

应用启动现在会在 SQLite + `LOCAL_CLIP` 或 `CLIP_COMBINED` 模式下装配 `RefillRuntime`，恢复并消费已经提交的补数 outbox；`LOCAL_EXACT` 和未配置持久账本的运行不启用。补数权限上下文只允许对批准库执行 `search`，不能借此发现新库或读取媒体。

`maintain_target/manual` 结果预览在有缺口时显示 `NEED_USER_INPUT`，不在结果发布阶段凭空判断搜索位置是否耗尽。`GET /v1/retrieval/tasks/{task_id}/refill` 使用当前结果有效数、当前批准位置和已发出指纹重新运行决策；`POST` 只接收当前 revision/generation/task version、位置指纹和 `action=confirm`，不接收检索词、库、索引或查询参数。确认后先原子预留再投递；重复确认同一位置返回原 reservation。已有未完成 iteration 时预览为 `wait`，不会并行发出下一位置。

Chat 结果区现按补数预览显示三种状态：有批准位置时展示原确认检索词、库和位置，并只提供“确认启动本轮补数”；已有 iteration 时展示等待；当前批准位置不可继续时才提供生成新检索词草稿的入口。浏览器提交体仍只有执行 scope、位置指纹和确认动作，不回传检索词或库。

## LOCAL_CLIP 增量候选批次

同一批准 plan set 在补数后可以按当前 task version 生成新的候选清单提案。提案仍只纳入已经提交、能解析为稳定 `MediaRecord` 且具有内容摘要的命中；首批清单要求首次检索结束，补数清单允许停在该轮已提交的连续页前缀，绝不读取或推测尚未提交的下一页。

服务端会读取同一 revision/generation、同一 plan digest 已发布清单中的稳定 candidate ID，并从本轮提案排除已见身份。历史清单保持可读；当前清单按 task version 选择最新已发布批次。同一素材重复命中不会再次进入清洗输入，但新素材仍保留本轮的命中证据。每个非空增量提案仍须由发起人显式确认，补检索确认不等于候选清单确认；空增量批次不能发布。

Chat 必须将当前 task version 的增量候选清单作为独立确认卡展示，并在 task version 前移时清除页面中旧清单的可执行状态。浏览器只回传提案摘要、预期 task version 和确认动作；不能借清单确认修改候选、检索词或来源。

如果当前任务已有发布的精确结果，确认增量候选清单**不得**启动仍按“单批覆盖”语义发布的清洗作业。当前 LOCAL_CLIP 路径已具备增量发布所需的显式血缘与累计合并：每个新结果记录直接父结果 ID/摘要和全部来源 cleaning operation；发布与重启读取时递归重建、重新授权并核对每一批的 manifest、规则、generation 和清洗结果摘要。父链不完整、循环、摘要漂移或范围不一致时失败关闭。

累计结果按内容摘要跨批精确去重，同内容只计一次有效，稳定选择 candidate identity 最小的代表；批内与跨批重复数累计，fail/unknown 仅累计审计数且不进入有效集。合并后按全局稳定顺序重新切分入选/候补，以累计有效唯一数重算缺口和停止原因。历史 operation 仍属于当前累计结果的受控来源，清洗步骤和授权素材可继续读取。旧无血缘字段的 v1 快照保持原摘要兼容读取，新累计快照使用 v2 摘要。

该能力当前只接入 `LOCAL_CLIP` 复用的 plan-set 候选清单路径：Chat 人工确认增量清单后可继续进入 VLM，发布使用上述累计语义。确认 `maintain_target/auto` 后，累计结果仍有缺口会在同一事务写入持久自动补数事件；协调器重验当前结果版本、确认策略、权限和准确查询位置后自动预留并派发下一页，重启恢复未投事件。

当检索、清洗、补数三个节点最初同时明确为 `auto` 时，成功提交补检索还会在派发事务写入 `auto_refill_intake_outbox`。该事件分阶段持久化候选 manifest 和清洗 proposal：自动入口重新校验当前 scope 与冻结模式，生成只含未见稳定身份的增量清单；空增量不发布，非空清单以 reservation 派生的稳定键确认；随后只从当前已确认规则和冻结模型构造清洗提案并批准，交给原有可恢复清洗队列。进程在任一阶段中断时从保存阶段或幂等确认记录续跑。仅补数节点为 `auto`、普通人工任务、旧 scope 和模式漂移均不会越过人工门禁。

正式方案可冻结 `refill_limits={max_rounds,max_vlm_calls,no_progress_rounds}`。后台模式默认不设置统一轮次或调用上限，两个上限以 `null` 表示不限；当前 LOCAL_CLIP 策略默认连续 2 个已完成补数批次无新增唯一有效素材即停止，任务可明确确认其他正整数窗口。限制属于正式方案，运行中不能由 Agent 或页面静默扩大。

每个 reservation 在 `refill_iteration_metrics` 保存派发前有效数。空增量直接完成该 iteration；非空增量在 VLM 批准前按清洗提案的 `estimated_vlm_calls` 原子预留累计预算，并把 cleaning operation 与 reservation 持久绑定，累计结果发布事件再记录完成后有效数。只有空增量或完整清洗结果会推进无进展窗口；排队、可重试失败和重复事件不推进。有效唯一数增长会清零连续无进展水位。

决策顺序为：达标、固定输入结束、已有持久停止、连续无进展、轮次余量、批准位置、人工/自动门禁。达到限制分别保存 `NO_PROGRESS`、`EXECUTION_LIMIT_REACHED` 或 `VLM_CALL_LIMIT_REACHED`，后续重启和只读结果继续显示同一原因；VLM 预算不足时不批准或启动该清洗作业。Chat 同时展示已发轮次、已完成轮次、连续无进展及已预留 VLM 调用量。

至此 LOCAL_CLIP 在三节点均明确为 `auto` 时具备有界自动循环：结果缺口→补检索→增量候选→VLM→累计去重重算→继续或停止。组合 CLIP 尚未接入等价的增量候选清单；除了受信命中映射，还需先接入受控媒体读取、撤权复验和内容摘要网关。真实费用对账、截止时间、租户公平调度和跨实例租约仍属于 R23，不能用本地调用预留冒充账单。

## 分组配额补数（R15 切片）

确认了组配额的任务，补数预览只保留仍能缩小缺口的位置：按位置查询文本对应的已确认分支来源组判断，来源组缺口均为 0 的位置不再提供或自动派发，只命中原始需求分支的位置也不用于配额补数（其候选无法归属）。多个缺口组时先补缺口最大的组，同缺口保持原批准顺序。缺口组没有剩余批准位置时为 `SEARCH_STRATEGIES_EXHAUSTED`，即使其他组还有可翻页位置。预览额外返回 `group_gaps` 与下一位置的 `group_labels`。

该筛选每次从当前结果的组缺口与确认方案重新计算，不新增持久状态，也不改变位置指纹；重启恢复、已签发预留、轮次/VLM 预算与无进展窗口沿用既有逻辑。由于 `accepted_count` 只计已分配素材，已满组新增的候补不会推进有效数，仍计入连续无进展。
