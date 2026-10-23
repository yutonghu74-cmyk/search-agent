# core 精确内容计数预览交接

## 检测器发布摘要批准持久化（2026-10-22）

- core / `codex/task-core` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/core`；发布基线 `d9a48e61478ccdccf28cb93998fc4b9f8132f018`，任务卡 `docs/dev/tasks/detector-release-approval-storage.json`；状态 READY_FOR_REVIEW。
- 修改 `adapters/task_storage/multi_rule.py`、`sqlite.py`、`tests/core/test_multi_rule_approval.py` 与本交接。`prepare` 现在要求受信调用方以仅关键字参数传入合法 `detector_release_digest`，并将其写入提案、提案摘要、批准作业和持久化快照；更换发布摘要得到不同提案，原确认幂等键不能复用。
- SQLite 白名单解码只对旧 `CleaningApprovalProposal` / `CleaningBinding` 缺少这一字段时补 `None`；其他缺字段、未知字段仍拒绝。旧对象保持 v1 摘要并可恢复审计，但 core 不猜测或补写当前发布；integration 执行入口必须用共享严格校验拒绝它。
- 定向验证：`PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest core.test_multi_rule_approval core.test_cleaning_result_storage -q`，14 项通过；`git diff --check` 通过。因本轮修改公共字段落盘与持久化解码，按仓库规则另运行 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`，384 项通过；输出中的 LOCAL GATE FAIL 是既有负例夹具。
- 交付门禁：`python3 tools/collab_gate.py check --role core --base d9a48e61478ccdccf28cb93998fc4b9f8132f018` 退出码 0，SCOPE PASS、门禁内部 384 项通过、LOCAL GATE PASS；输出中的 LOCAL GATE FAIL 是既有负例夹具。门禁已覆盖的测试未另行第三次手工回归。
- NOT_RUN：真实 DJ、真实检测器/模型制品、生产账本迁移、ClickHouse/Milvus/W3、内网数据；core 只持久化调用方提供的受信发布身份，不自行加载或选择发布。

## 当前任务：input-only 精确去重预览原子发布（2026-10-22）

- core / `codex/task-core` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/core`；执行基线 `4cfd13ebb9657be3dd3728d5571576b4bc720688`，任务卡 `docs/dev/exact-result-publication-task.md`；状态 READY_FOR_REVIEW。
- 修改 `adapters/task_storage/multi_rule.py`、`sqlite.py`，新增 `tests/core/test_exact_result_publication.py` 及本交接。消费 A 已冻结的 `ExactResultSetPreview`；多规则账本 format 升为 3，旧 format 显式拒绝，不静默迁移或重置。
- `publish_preview` 仅消费已批准且完整持久化的清洗结果，在单个 `BEGIN IMMEDIATE` 事务中重验创建人、项目/素材访问、task/revision/generation/task_version/operation、确认规则及固定 input-only 精确内容策略；原子写入不可变预览、当前指针、统计事件，并将任务版本加一、阶段推进到 `refill`、清除活动清洗 operation。重放和 12 次多连接并发只有一次效果；`get_current_result_set` 每次读取重新授权并从源结果确定性重建校验。
- 返回 accepted/eligible/reserve/duplicate/fail/unknown/gap/stop_reason 和可追溯代表；固定输入不足时停止原因为 `SEARCH_STRATEGIES_EXHAUSTED`，语义只限已批准 input-only 范围，不代表全库穷尽。`refill_dispatched=false`，不创建新检索、清洗或 delivery。
- 2026-10-22（Asia/Shanghai）：定向 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_exact_result_publication.py' -q` 退出码 0，6 项通过；完整回归 350 项通过。`python3 tools/collab_gate.py check --role core --base 4cfd13ebb9657be3dd3728d5571576b4bc720688` 退出码 0，SCOPE PASS / LOCAL GATE PASS；输出中的 `LOCAL GATE FAIL` 是门禁负向夹具预期。`git diff --check` 通过。
- 覆盖：重启读回及任务推进、并发幂等、确认策略快照被改写/旧 generation 拒绝、撤权和后续 task_version 变化阻断读取、序列化失败时任务/指针/事件全回滚、持久化源结果被篡改后重启失败关闭。公共契约单测另覆盖精确去重、稳定代表、target/reserve 拆分、fail/unknown 不计与摘要变化。
- NOT_RUN：自动补数、跨 batch 去重、相似去重、正式 delivery/result_set API、生产数据库与 outbox relay、真实 ClickHouse/Milvus/W3/DJ、断电/网络文件系统、性能与业务质量。继续自动补数前必须由 A 扩展 QuerySpec/ExecutionRules 与检索推进契约，core 不私建第二套协议。

## 当前任务：多规则清洗结果本地发布（2026-10-22）

- core / `codex/task-core` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/core`；执行基线 `1c09d1fd69e1cfc844dc7ed74ae24e7a42cb3c03`，任务卡 `docs/dev/cleaning-result-storage-task.md`；状态 READY_FOR_REVIEW。
- 修改 `adapters/task_storage/multi_rule.py`、`sqlite.py`，新增 `tests/core/test_cleaning_result_storage.py`及本交接。多规则账本 format 升为 2，新增不可变 `CleaningJobResult` 与待发事件；旧 format 显式拒绝，本地开发账本不自动迁移或重置。
- `publish_result` 在单个 `BEGIN IMMEDIATE` 事务中重新校验创建人、项目/素材授权、task/revision/generation/task_version/operation、确认规则、清单/配方及结果完整性；同结果重放无第二次效果，同 operation 异结果冲突。`get_result` 每次读取仍重验当前权限和版本。
- 定向新测试 6 项通过：重启读回/幂等、12 次多连接并发仅一次发布、异结果/伪造候选拒绝、跨用户/撤权/旧 generation 拒绝、发布后撤权/版本变更阻断读与重放、序列化失败结果/事件全回滚。`python3 tools/collab_gate.py check --role core --base 1c09d1fd69e1cfc844dc7ed74ae24e7a42cb3c03` 退出码 0，SCOPE PASS / LOCAL GATE PASS / 339 项完整回归通过；输出中 `LOCAL GATE FAIL` 是已有门禁负例夹具。
- 发布不修改 task_version/run 状态，不计算 accepted_count，不派发补数。NOT_RUN：生产数据库与 outbox relay、真实 DJ 多规则、ClickHouse/Milvus/W3、断电/网络文件系统、性能与业务质量。下一切片由业务计数层消费已发布结果，实现去重有效数与补数决策。

## 当前任务：持久候选清单去重预览

- core / codex/task-core / var/worktrees/core；基线 1c89f59f00707d739e1731c4bf7334fc0bed25d1；范围 core/counting.py、tests/core/test_manifest_counting.py、本交接。没有新增共享字段/接口，复用 CleaningCatalog 已发布契约。
- 依据 v1.9 13.5/13.5.1、A20/A36/A45/A50 的本地子集。已确认清单→清洗结果→只读精确内容去重；返回前重验当前清单和媒体权限，旧版本/撤权失败关闭。
- 验收：无临时路径也可预览；相同内容不同来源保留记录仅选一个代表；fail/unknown 不计合格；当前媒体撤权、处理途中撤权/任务变化拒绝；结果不写账本、accepted_count=null、无补数。只做单清洗作业固定宽度规则，不冒充正式 result_set。
- 本轮允许职责内开发/本地测试、验证后本地提交与审查集成；不安装依赖、推送、接内网。A 后续可在独立工作区更新并运行既有合成真实 DJ smoke；此工作区不运行 DJ。
- 2026-10-22 14:08（Asia/Shanghai）：增加返回前清单/媒体授权与完整输入二次核对；8 项新组合测试。`python3 tools/collab_gate.py check --role core --base 1c89f59f00707d739e1731c4bf7334fc0bed25d1` 退出码 0，范围检查和 232 项测试通过（224+8）；git diff --check 通过。
- 实证修正前轮假设：CountingPreviewService 本身不读路径，使用 catalog.resolve；PublishedManifestCatalog 的空路径逻辑候选可直接复用。无需复制另一套模型或去重算法。确认相同内容独立来源保留两个清单成员、只选一个 pass 代表，撤销重复来源亦拒绝整次预览、不静默换来源。
- 本轮模拟验证，不执行 DJ；摘要对应已验证清洗输入，不保证远端仍可下载。不发布正式 result_set、accepted_count 仍 null、不跨作业聚合或自动补数。

## 当前任务：候选清单本地账本

- core / codex/task-core / var/worktrees/core；基线 8d7dcc8b39a672ad174cf91704a84e5c66eec883；范围 task_storage/、tests/core/、本交接，验收见 docs/dev/candidate-ledger-task.md。
- 实现 CandidateManifestRepository；本地 SQLite，同任务/检索账本事务读取栅栏，发布清单/幂等键/待发事件一起保存。无真实媒体/内网或自动清洗。
- 2026-10-22 12:15（Asia/Shanghai）完成候选账本与 20 项测试；修改 candidates.py、sqlite.py（类型注册及新表兼容）、test_candidate_storage.py、本交接。旧任务/检索 payload 不变；新 candidate_ledger format=1，16 MB 限制，不迁移或修改用户已有运行库。旧程序不认识新增表，不能直接降级读该库。
- `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q` 初轮 202 项通过；新增空页和损坏事件检查后，`python3 tools/collab_gate.py check --role core --base 8d7dcc8b39a672ad174cf91704a84e5c66eec883` 退出码 0，范围和全部 204 项通过（178 原基线 + 6 契约 + 20 存储/模拟集成）。git diff --check 通过。
- 验证：重启/独立进程恢复、六连接并发同提案多键单次发布、同键异体冲突、确认/读授权、第二次授权失败与序列化失败回滚、跨任务/租户隔离、旧代次/计划拒绝、task_version 递增后清单读取且旧检索仍拒绝、空页无清洗、存储损坏不重置。
- 模拟服务组合：真实本地物化器 + 合成字节网关 + SQLite，预览后重新读取确认；更改字节拒绝发布；临时目录清理，任务状态/有效数量不变。不是真实召回、DJ 或内网验收。
- 未验证/下一步：受控清单再次物化与 CleaningCatalog 生命周期装配、跨页候选池、当前授权投影、后台恢复/relay、生产数据库、真实素材可用性。仅保存引用，不承诺远端内容永久可用；跨系统 ACL 与 SQLite 非原子。

- 角色 / 分支：core / codex/task-core。
- 独立 worktree：/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/core。
- 分派任务：docs/dev/core-counting-task.md；规格 7.11、7.14、8.1 局部场景。
- 基线 SHA：b5bc29794087fbf79914f25b1f7fe4cebcd676d6；本切片尚未提交或合并。
- 状态：READY_FOR_REVIEW。
- 修改仅在 src/dsh_retrieval/core/、tests/core/ 与本文件。未修改共享契约、责任表、检查器或旧 DJ。
- 实现：只读 CountingPreviewService；读取当前完整清洗结果，重新验证项目访问/媒体内容/任务版本，pass 按冻结内容摘要去重，稳定代表与目标截取，返回单规则 unique_pass_count/preview_gap。fail/unknown 不计；accepted_count 保持 null；不派发补数、不写业务状态。
- 实际单测：2026-10-21，独立 worktree 内 `PYTHONPATH=src python3 -m unittest discover -s tests`，56 项通过，新增 8 项计数测试。
- 角色检查：`python3 tools/collab_gate.py check --role core --base b5bc29794087fbf79914f25b1f7fe4cebcd676d6`，退出码 0，SCOPE PASS / LOCAL GATE PASS，56 项测试通过；不是全量业务验收。
- 模拟/真实：新增计数测试使用 fake 清洗执行器及固定文件，不声称真实检索联测；基线已有真实 DJ 证据。本工作区未复制/共享可变 DJ 环境，未重跑真实 DJ。
- NOT_RUN：内网 ClickHouse/Milvus/W3、持久化、恢复、性能、跨轮发布。读取末尾复核不等于跨系统原子授权；结果只是带版本的即时预览。
- 后续由 A 冻结模拟检索/媒体授权/跨轮结果集公共契约，再分派后续工作。本切片不是完整业务有效计数或正式结果发布，不会把单个宽度规则冒充全部自然语言业务条件。

## 串联验证补充

新增 tests/core/test_pipeline.py：从保存草稿、发起人确认、准备清洗、明确确认、轮询清洗到去重预览，全部通过服务入口；不直接调用内存仓库的结果发布方法。固定输入和 Worker 均为 SIMULATED，不冒充检索或真实 DJ 联测。

固定 5 个候选，3 pass（其中两个内容相同）/1 fail/1 unknown，得到 unique_pass_count=2、duplicate_count=1、目标 3 的 preview_gap=1，accepted_count=null；input_only 不补数，任务不自动完成。另覆盖 Worker 整批失败、清洗取消、成功后撤权。完整单测现为 60 项通过。

## 待 A 冻结的候选接入契约提案（未实现）

- 原因：当前 LocalCleaningCatalog 由服务配置固定清单，不具备检索结果到不可变清单的发布接口；不能让检索适配器直接改它的 manifests 字典作为正式接口。
- 建议输入：服务端绑定 tenant/project/task/revision/generation，已批准 plan_id/plan_digest，稳定 operation_id，versioned candidate_id/asset_version/media_ref 列表。客户端不得传路径或任意 URL。
- 建议输出示例：`{"manifest_ref":"manifest_fixture_001","input_manifest_digest":"sha256:<实际摘要>","candidate_count":5,"source_mode":"simulated"}`。示例摘要是占位，运行时必须计算。
- 保证：同 operation 同输入重放；异摘要冲突；拒绝旧代次和未授权媒体；仅完整清单可以准备清洗；空召回返回明确空结果，不伪造清洗成功；清洗仍须经过发起人确认。
- 兼容性：保留 CleaningService.prepare 的 manifest_ref 入参；以新清单适配器实现既有 CleaningCatalog.resolve。候选发布能力须另由 A 定义公共端口，不在 core 内私建接口。
- 受影响角色：A 公共契约/装配，retrieval 候选来源与清单发布，cleaning 媒体物化，core 只读消费。
- 验收：固定候选 5 条 → 明确清洗批准 → 真实 DJ → 去重预览；包含重复来源、素材版本变化、跨租户、重试和空召回。不启用范围外补数，不将 simulated 标成内网通过。
- 集成阻塞：本切片尚未提交/合并；当前用户历史授权为建立本地基线，未包含后续自动合并。需要明确本地提交/合并授权后由 A 发布下一基线和 retrieval 任务卡；不推送。

## 本地持久化切片（前文为历史）

- 用户已统一授权测试后的本地提交/合并。角色 core / codex/task-core / var/worktrees/core，基线 551eae4052fbc9e1dacf7c086ecc2972c0dc7d39；任务卡 docs/dev/storage-task.md。
- 新增 adapters/task_storage/sqlite.py：现有仓库接口的本地 SQLite 实现，每次操作短事务读取快照，写事务 BEGIN IMMEDIATE，FULL 同步；白名单 JSON 类型编码，不使用 pickle。结果/任务/幂等/generation 在同一快照提交。16 MB 开发上限；非生产 outbox、非高吞吐关系表设计。
- 新增 8 项测试：新实例重放、新进程恢复结果、18 次跨连接并发确认一次、重启后取消栅栏、注入序列化失败事务回滚、未知格式/损坏拒绝、身份隔离、operation 重放。完整测试 81 项通过；无额外依赖安装，无真实数据。
- 生产持久性、断电故障注入、网络文件系统、内网授权竞争与完整运行调度 NOT_RUN；只支持本机文件，不替用户决定内网账本。
- 未改公共端口、共享模型或检查器；交付前运行该基线 core 角色门禁。A 后续接演示恢复入口，候选临时目录失效仍须明确报错，不能悄悄使用新内容。

## 2026-10-22 检索计划账本任务卡（本轮）

- 角色 core / codex/task-core / /Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/core；基线 7e9e33832352b22e2d68fcb82bdb86cca44973c1。
- 用户继续开发，沿用本地测试后提交/审查集成授权；不推送、部署、安装依赖或接内网。本轮单窗口，无其他修改者。
- 范围：adapters/task_storage/、tests/core/、本交接；消费 contracts/retrieval-plan.md 与 RetrievalPlanRepository，不改公共协议、模型及检查器。
- 实现目标：独立 SQLite 检索账本与已有 local_ledger 同库同事务；原子确认/CAS/多键幂等、页预约与游标来源、过期回传拒绝、页与本地待发事件原子记录、新连接/进程恢复。
- 验收：v1.9 6.5、11.2.2、13.5；A12/A14/A15/A16/A20/A37/A49 的本地存储子集。并发确认只一次；失败回滚；撤权拒绝；空页不创建清洗/不标业务完成；旧 epoch/代次不能发布；保持旧任务账本可读。
- 存储策略：保留原 local_ledger format=1 数据，新增独立版本化 retrieval_ledger 表；旧二进制遇扩展表会失败关闭，不承诺无迁移降级。每个快照仍有开发大小上限；不是生产 outbox 消费器或真实内网授权事务。
- 状态：开发中。测试与实际结果待补；不运行 DSH 或真实 DJ。

### 本轮交付结果

- 状态 READY_FOR_REVIEW（本地账本切片）；代码待本地提交后由 A 审查集成。
- 文件：adapters/task_storage/retrieval.py、adapters/task_storage/sqlite.py、tests/core/test_retrieval_storage.py、本交接文件；未改公共模型/协议/责任表/检查器。
- 实现 SQLiteRetrievalPlanRepository：共用任务数据库的 BEGIN IMMEDIATE；计划确认/CAS/epoch/当前指针/幂等同事务；重复提案确认与同页多键预约仅一次效果；回传验证 operation、scope、查询及游标/快照，记录不可变页和本地待发事件。重放旧确认返回历史记录但不重新激活；旧计划执行拒绝。
- 权限：每个入口重新核验任务所有人、项目、库权限；未配置/撤权/依赖不可用均失败关闭。写入前再次检查不等于对外部 ACL 加锁。没有读取真实内网授权或数据。
- 旧 local_ledger payload/format=1 不迁移、不清空；检索另用 retrieval_ledger format=1，每份快照最多 16 MB。旧二进制会拒绝带新表的文件，不支持无条件降级。SQLite 全快照串行写仅供开发，未改变生产选型。
- 验证：2026-10-22 10:26（Asia/Shanghai），Python 3.13.3；`python3 tools/collab_gate.py check --role core --base 7e9e33832352b22e2d68fcb82bdb86cca44973c1` 退出码 0，SCOPE PASS / LOCAL GATE PASS，144 项通过（原 123 + 新 21）；git diff --check 通过。
- 新测试覆盖：18 次跨连接确认、12 次并发预约/回传各一次效果；事务序列化故障回滚；新进程恢复批准计划及原 operation；变更正文冲突；旧确认不激活；旧代次/取消拒绝；游标前驱与不可变页；空页不清洗/不完成；撤权及授权故障；原账本兼容与检索格式损坏；模拟网关→页校验→账本的两页串联。
- NOT_RUN：真实 DJ/ClickHouse/Milvus/W3、断电/网络文件系统故障、生产性能、跨系统授权原子性、事件 relay、业务 UI/HTTP/清洗装配。本地事件只是待发记录，没有发送或消费保证。
- 约束：每个 task 的 plan_id 不可变；新提案使用新 ID 和账本下一 epoch（不符返回期望 epoch）。get_current 遇陈旧 task scope 会拒绝，不返回可执行旧计划。已有清洗尚未终结时不允许新检索计划确认；支持流水线并发需要另行冻结兼容承接契约。
- 后续接口缺口：现有协议可通过同 key 恢复预约，但缺少按任务列举待执行作业/读取已提交页的公共接口。下一步由 A 冻结恢复读取与候选清单发布契约，再装配自动恢复及清洗；本轮不私增公共接口，不宣称全业务闭环。

## 2026-10-22 授权读取实施任务

- 角色 core / codex/task-core / var/worktrees/core；新基线 8131b443b3222285d26a313f65574d4e52b3e913，已快进同步；任务卡 docs/dev/retrieval-read-task.md。
- 范围仅 task_storage/retrieval.py、tests/core/、本记录。实现已冻结 get_operation/list_pending；保持原数据库和写入语义，无 schema 变更。
- 验收：跨身份/任务、旧 scope/epoch、暂停取消及撤权拒绝；新进程无需旧幂等键可发现预约；已提交空页不算待执行；读取不写任务/事件；故障不变空结果。
- 状态开发中，完成后记录真实测试结果并本地提交/审查集成。不访问内网、不运行真实 DJ/DSH、不自动派发。

### 授权读取交付

- READY_FOR_REVIEW：已实现 get_operation/list_pending，返回不可变 RetrievalOperation；只读当前计划，库/项目授权末尾再次核验；未知/跨任务 operation 隐藏；旧版本、取消、撤权、故障拒绝。无需新表或迁移。
- 2026-10-22 10:50（Asia/Shanghai），Python 3.13.3：`python3 tools/collab_gate.py check --role core --base 8131b443b3222285d26a313f65574d4e52b3e913` 退出码 0，SCOPE PASS / LOCAL GATE PASS，158 项通过（原 144 + A 模型 2 + 新读取 12）。git diff --check 通过。
- 最初辅助 TestCase 的直接导入导致 21 项重复发现，已改用模块引用，未删除测试或改变发现规则。独立检查确认 158 个测试 ID 均唯一；早期 179 次执行数不作为验收数量。
- 新测试覆盖新连接/新进程发现、读取待执行与已提交空/非空页、只读无写入、跨任务/租户/用户、旧 generation/plan、暂停/取消/FINALIZING、权限撤销/故障、末尾授权重查和存储损坏。
- 数据库新增读取不自动执行任务，不持有 lease；多读者可见同一预约，不代表外部请求恰好一次。原始命中不能替代媒体授权。候选清单发布、媒体物化、HTTP/清洗装配、自动恢复执行器和事件 relay 均 NOT_RUN/未实现。
# 多规则清洗本地批准账本（2026-10-22）

- 角色 core；独立 worktree `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/core`；分支 `codex/task-core`；执行基线 `18f036017efbbe1215c263bb537d93d8361b2976`，任务卡 `docs/dev/multi-rule-approval-task.md`。本节状态 READY_FOR_REVIEW，待 A 验收。
- 改动：`src/dsh_retrieval/adapters/task_storage/multi_rule.py` 新建本地 SQLite 批准账本；`sqlite.py` 允许新表并注册不可变契约类型；`tests/core/test_multi_rule_approval.py` 覆盖正式确认规则/已授权候选清单绑定、显式确认、任务版本与事件同事务、并发幂等、跨连接恢复、撤权、旧版本、序列化失败回滚；本交接记录真实结果。
- 接口：`prepare(actor, task_id, manifest_ref, recipe)` 仅保存待确认提案，不推进任务；`confirm(actor, task_id, proposal_digest, key, expected_task_version, action="confirm")` 返回 `CleaningApproval` 和 replay 标志；`get_current` 在任务、清单、权限和版本仍当前时读批准作业。新接口供受信业务服务调用，不是公开 HTTP 或模型工具。调用方必须由受信编译器构造配方、向发起人展示完整条件与摘要；账本再次比对正式 QuerySpec 硬规则，但尚未持久验证解释草稿本身的来源关系。
- 语义边界：只记录待执行作业，不运行检测器或 DJ、不发布逐素材结果、不计算 accepted_count。`approval.job` 可供后续受控 Worker 使用；发布仍须另建原子 fencing 和结果完整性检查。SQLite 与本地授权夹具不是生产存储或公司内网验收；新表会使旧版二进制拒绝打开此账本，正式迁移另行处理。没有安装依赖、访问内网、推送或部署。
- 2026-10-22（Asia/Shanghai）：`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_multi_rule_approval.py -q` 退出码 0，6 项通过；`python3 tools/collab_gate.py check --role core --base 18f036017efbbe1215c263bb537d93d8361b2976` 退出码 0，SCOPE PASS / LOCAL GATE PASS，325 项完整回归通过（319+6）。首次定向命令以 `-s tests/core` 运行因导入路径不含 `tests` 报错，改为标准 `-s tests` 后通过；首次发现任务对象返回副本导致事务未推进，已修复并重跑。测试中的 LOCAL GATE FAIL 为负向夹具预期。
# Chat 反馈草稿与版本事务（2026-10-22）

- 角色：core；worktree `var/worktrees/core`；分支 `codex/task-core`；基线 `cd3b79f8e86a5db71e83b0e0ad8d7bb2b5470937`；任务卡 `docs/dev/tasks/chat-feedback-core.json`；22:49 开工。
- 范围：`service.py`、内存/SQLite 任务账本、core 定向测试与本交接。目标是反馈保存不改变正式运行，确认才原子发布新 revision 并提升 generation，拒绝不影响当前版本。
- 边界：首版只追加反馈文本到完整确认方案，未做自然语言规则编译；HTTP/UI、真实检索/DJ/W3/内网均不在本切片。
- 22:57 里程碑：内存与 SQLite 事务、服务校验、pending task 视图及版本历史已实现；定向 `tests.core.test_feedback tests.core.test_sqlite_storage tests.test_service` 共 22 项通过。
- 22:59 交付：`python3 tools/collab_gate.py check --role core --base cd3b79f8e86a5db71e83b0e0ad8d7bb2b5470937` 为 SCOPE PASS、完整 400 项通过、LOCAL GATE PASS；输出中的 `LOCAL GATE FAIL` 是既有负向门禁夹具。
- 未验证：HTTP/UI 尚未接入；真实检索、DJ、W3、模型语义编译和内网均 NOT_RUN。本地 SQLite 仍不是生产存储。
# Chat 历史需求读取（2026-10-22）

- 角色：core；worktree `var/worktrees/core`；分支 `codex/task-core`；基线 `3304adf7ef1885e50a0dfbd70c86aafb63043b9f`；任务卡 `docs/dev/tasks/chat-history-core.json`；23:26 开工。
- 范围：当前创建人在指定项目内的已确认任务列表、权限重验、稳定有界排序、SQLite 读取及 core 测试。它是正式需求/版本入口，不是确认前逐句 conversation_message 历史。
- 23:29 里程碑：owner/project 过滤、当前权限重验、100 条上限、稳定排序、pending 摘要和 SQLite 重启读取已实现；定向 12 项通过。
- 23:31 交付：`python3 tools/collab_gate.py check --role core --base 3304adf7ef1885e50a0dfbd70c86aafb63043b9f` 为 SCOPE PASS、完整 409 项通过、LOCAL GATE PASS；输出中的 `LOCAL GATE FAIL` 是既有负向门禁夹具。
- 未验证：HTTP/UI、完整 `conversation_message` 草稿历史、真实 W3/内网不在本切片。
# 独立草稿会话账本持久事务（2026-10-23）

- B core / `codex/task-core` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/core`；任务卡 `docs/dev/tasks/conversation-message-storage.json`，基线 `2877e6ce7e0883378f3b30471361d43773790f78`；状态 READY_FOR_REVIEW。
- 范围：`service.py`、内存/SQLite task repository、`tests/core/test_conversation_storage.py` 和本交接。实现需求草稿/反馈消息与对应草稿同事务保存，确认时写独立 task/revision 关联；消息不进入正式 revision 时间线。
- 验证覆盖：新需求→确认→反馈→再确认的消息与两次关联；幂等不重复；错误绑定全回滚；创建人/项目撤权；SQLite 重启恢复；移除 conversation 容器及 Task/Draft 新字段的旧快照兼容。`PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.core.test_conversation_storage tests.test_drafts tests.core.test_feedback tests.core.test_sqlite_storage -q`：35 项通过、退出码 0。`python3 tools/collab_gate.py check --role core --base 2877e6ce7e0883378f3b30471361d43773790f78`：SCOPE PASS、420 项通过、LOCAL GATE PASS、退出码 0；其中 `LOCAL GATE FAIL` 为既有负向夹具输出。按用户要求未在门禁外重复完整回归。
- 不实现 HTTP/UI、附件、assistant 消息、保留/删除策略；不访问内网、真实业务数据、W3 或 DJ。
# 多分支整组批准持久化（2026-10-23）

- READY_FOR_REVIEW；角色 core，分支 codex/task-core，独立 worktree /Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/core；任务卡 docs/dev/tasks/multi-branch-storage.json；基线 cc9d14c6e3b5f27484ed47299babbfc6ba04fdc7。
- 新增 SQLitePlanSetApprovalRepository，仅实现 put_proposal/confirm/get_current；新表与现有 local_ledger 同一事务。序列化注册 PreparedQuery/RetrievalPlanSet，旧格式不重置；无共享模型变更。
- 全成员权限、scope/CAS/摘要/epoch 校验；一次任务版本增长、所有成员 scope 重绑定、当前集合/幂等/outbox 同事务。相同 proposal 多 key 一次效果；旧批准重放仅返回旧记录，不改变当前集合。
- 定向 `PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.core.test_plan_set_storage tests.test_multi_branch_contracts -q`：初次 12 项 PASS，补独立进程恢复测试后 13 项 PASS。
- `python3 tools/collab_gate.py check --role core --base cc9d14c6e3b5f27484ed47299babbfc6ba04fdc7`：SCOPE PASS、465 项 PASS、LOCAL GATE PASS，退出码 0（中间 LOCAL GATE FAIL 为既有负向夹具）；git diff --check PASS。未在门禁外重复完整回归。
- 不实现 reserve_page/commit_page、调度或 HTTP，不声称完整实现 RetrievalPlanSetRepository。真实目录/编码器/内网未运行。下一步实现成员级分页预留与回传事务，复用已发布协议。
# 多分支分页预留与回传（2026-10-23）

- READY_FOR_REVIEW；core / codex/task-core；worktree /Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/core；任务卡 docs/dev/tasks/multi-branch-pages.json；基线 73ab359f118af480174fb917c4a2af6c01f12ce7。
- plan_set_ledger 格式升至 2：只对精确旧格式 1 增补分页字典并原子迁移；新版缺字段视为损坏，不重置。reserve_page/commit_page 复用已发布协议与 validate_member_execution/validate_page，检查整组/成员/版本、独立游标链、请求与结果幂等、上限、权限及不可变页面；页面/outbox 同事务，不变更任务有效数。
- 定向命令 `PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.core.test_plan_set_pages tests.core.test_plan_set_storage -q`：16 项 PASS。首次 14 项虽 PASS 但暴露测试 SQLite 连接未关闭警告，已用 closing 修复；复测无警告。覆盖格式迁移、打开新仓库恢复、重复/并发、跨分支游标、错误快照、旧集合/generation、撤权和序列化回滚、在途上限。
- `python3 tools/collab_gate.py check --role core --base 73ab359f118af480174fb917c4a2af6c01f12ce7`：SCOPE PASS、474 项 PASS、LOCAL GATE PASS，退出码 0；中间 LOCAL GATE FAIL 来自既有负例。git diff --check PASS，未额外重复完整回归。
- 真实检索/内网/页面 NOT_RUN；没有后台 worker、领取租约或自动发起检索。下一步需冻结可恢复调度读取契约并装配执行器，不通过内部字典读取构造生产调度。
