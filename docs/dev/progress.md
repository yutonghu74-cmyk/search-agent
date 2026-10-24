# 开发进度看板

> 由 A 总控维护当前快照；各角色的可审计开发明细见 `docs/dev/handoffs/`。本页不是自动状态源，也不替代 Git、测试日志或角色门禁。

## 当前快照

- 2026-10-24：IN_PROGRESS（Chat 对话式 LLM 体验），任务卡 chat-conversational-llm.json，独立 `codex/chat-conversational-llm`，发布基线 `bafbf4c`。单一受信模型默认选中、对话中分析状态/助手回复/可展开证据、数量建议不覆盖人工编辑已实现；正在执行工作包门禁和 8085 真实模型浏览器验收。模型回复是对结构化分析的归纳，仍需用户审阅确认；不代表自主多轮代理。
- 2026-10-24：8085 本地体验已接用户提供的兼容接口与 `gpt-4o-mini`（私有配置/密钥在仓库外，8084 不变）。真实合成诉求调用返回经引文校验的 LLM 分析，浏览器显示待审阅分析卡及提单确认卡，未自动提单；`execution_allowed=false`。模型提示修正独立包 628 项门禁 PASS。当前标点被判未解析、能力未注册；真实反馈调用/长期质量/生产权限 NOT_RUN。
- 2026-10-24：READY_FOR_REVIEW（意图引文 occurrence 提示收紧），任务卡 intent-occurrence-prompt.json，基线 40f41cd、更新门禁基线 c9cda1c。真实 `gpt-4o-mini` 首次因 occurrence 写成字符位置被安全拒绝；收紧提示后 8086 合成诉求返回经校验的分析预览且不可自动执行，严格引文校验不变。628 项完整门禁 PASS；真实模型长期质量与生产权限联测 NOT_RUN。
- 2026-10-24：ACCEPTED（Chat LLM 逐条诉求分析），任务卡 chat-llm-analysis.json，独立 `codex/chat-llm-analysis`，基线 98f58c5，实施提交 dbc1be7 已合并主线。已接授权分析预览和提单/反馈入口；模型输出仅审阅、不覆盖原文或自动批准。18 项定向、628 项完整门禁及 JS 语法 PASS；8084 当前仍未配置模型，真实联测 NOT_RUN。
- 2026-10-24：ACCEPTED（本地首次检索 1–5），独立 `codex/first-search-local`，任务卡 first-search-local.json，基线 f18caee，实施提交 327ec3b 已合并主线。显式选库/整组确认/有界 HTTP tick/授权原始候选列表已接通；25 项定向、624 项完整工作包门禁、JavaScript 语法和 8084 双索引两批页面冒烟 PASS。缺映射候选失败关闭，原始命中不计为有效清洗数量。真实 Milvus/embedding/媒体缩略图/清洗有效数量仍 NOT_RUN；不得标为真实端到端完成。
- 2026-10-24：READY_FOR_REVIEW（R06 只读进度 HTTP 切片），独立 `codex/r06-progress-api` 工作包，基线 e43355f3dee2fe7b31f060792dd40f7e79051b8a。已实现已批准整组的授权 SQLite 进度投影及本地 demo 显式装配；7 项定向测试、603 项完整工作包门禁 PASS。HTTP 派发和真实向量网关仍未接入，8081/8082 运行服务未重启。
- 2026-10-24：READY_FOR_REVIEW（前端界面优化），A integration，任务卡 frontend-ui-optimization.json，实施基线 de1331562c2238178c7f47ab228241f0dc4dd1c8。当前 Chat 采用左历史 / 中对话 / 右侧新建与模型设置三栏；历史 Chat 可恢复续聊并本地重命名，API/密钥配置仅页面内存暂存，五节点以流水线展示已有结果或明确空态。最新 24 项 UI 定向、JavaScript 语法、差异格式及约 740px 浏览器验收 PASS；按用户要求未对本轮纯前端变化重复完整回归。此前同任务状态的 589 项完整回归与 integration 门禁证据仍保留，但不作为本轮新增交互的验证。真实模型/检索/自动清洗仍 NOT_RUN。
- 2026-10-24：READY_FOR_REVIEW（R06 本地协调切片），A integration 整组已批准计划的有界预留/请求/回传/重试已实现；B/core 只读恢复接口已提交 afaa09e。真实 SQLite 的独立进程恢复、跨分支游标、重试和撤权测试 9 项 PASS；core 与最终 integration 角色门禁各 588 项 PASS。真实检索网关、自动执行策略和生产租约仍 TODO，不改 8081/8082 服务或用户账本。
- 2026-10-24：READY_FOR_REVIEW（用户授权协作流程简化），独立 codex/workflow-simplification 工作包，基线 90793f88c21b2ca126bc50a8eed38ba7ef5261a0。新增可信工作包范围检查、跨模块授权、独立分支/交接与按风险测试；旧角色命令保持兼容。完整回归 573 项 PASS，差异格式检查 PASS；治理升级不自称通过旧角色范围门禁。R06 草稿及其他窗口未提交文件保持不动；详见 handoffs/workflow-simplification.md。
- 2026-10-24：ACCEPTED（物理索引配置校验切片）；A integration，任务卡 physical-index-registry.json，基线 949226583fae5e24dc29c54786f3932ade39d25a。完整模型/物理目标/读取声明、配置摘要漂移、类型化主键隔离及授权范围缓存键已实现；19 项定向、555 项角色门禁 PASS。真实驱动/连接健康/权限联测 NOT_RUN，未接现有 HTTP，8082 不变；下一步消费此契约接真实连接器。
- 2026-10-24：ACCEPTED（选库预览 API/UI），A integration，任务卡 routing-preview-ui.json，基线 c5a1e17dcc17a914f33998aaaf312b829496ef13。显式能力注册/配置漂移复验完成；36 项定向、543 项门禁 PASS。8082 已更新，HTTP 页面包含“查看选库结果（未执行）”；现有账本沿用，8081 不变。8082 尚无真实索引清单/模型密钥/能力配置，不能生成真实路由或执行检索。下一步需真实配置与编码器接入。
- 2026-10-24：ACCEPTED（后端接线切片），已保存配置到选库路由；任务卡 configured-routing.json，A integration / codex/integration，基线 449b64cc88892321ab35c3558ca0699daa1f40d4。授权后字面匹配、精确索引能力及配置版本栅栏完成；28 项定向、534 项角色门禁 PASS。未接页面路由入口、真实编码器/搜索，现有 8081/8082 服务未改。下一步是受信编码器接入与路由 API/UI；真实清单仍待提供。
- 2026-10-24：ACCEPTED（本地配置入口切片），索引配置页面/API 及显式本地目录装配；A integration，任务卡 catalog-config-ui.json，基线 ef6d8f7d80f52bf3eb3749c17a164fb4e88a5670。首轮定向 27 项、补充后配置 API/UI 7 项和角色门禁 524 项 PASS。独立配置授权默认拒绝；8081 未重启，浏览器视觉验收及真实模型/目录联测 NOT_RUN。下一步配置到选库路由装配和真实索引清单验证。
- 2026-10-24：ACCEPTED（R02 文本配置后端切片）；A integration / codex/integration，任务卡 text-catalog-config.json，发布基线 a35d420fae4eb94afd8609f94cba39c45f5f44dc。已有索引文本候选、授权/摘要校验、SQLite 原子确认和配置读取已实现；首轮 34 项定向、补充后配置 15 项、角色门禁 517 项 PASS。仅登记受信已有库，不物理建库。下一步配置页面/API、真实索引清单和路由装配；8081 未重启，真实模型/目录联测 NOT_RUN。
- 2026-10-24：ACCEPTED（R01 后端切片），后续总清单见 `remaining-roadmap.md`（R01–R29）。A integration / codex/integration，任务卡 query-routing.json，基线 dd2fd37ec872a4b01109706f45f2c1159af8905a；授权索引发现和整组路由准备已实现，24 项定向/502 项角色门禁 PASS。不同库按各自编码器准备计划；无权限、陈旧/混合版本和重复路由失败关闭。真实目录、向量库与编码器资料待提供；下一项 R02，当前无新页面入口或真实向量搜索。
- 2026-10-24：ACCEPTED（反馈清洗建议预览），任务卡 feedback-cleaning-suggestion.json，A integration / codex/integration，发布基线 32ce770be55c7c07279319aeabd860b14231e262。已实现当前反馈草稿的 LLM→候选 VLM 指令建议、调用前后版本复验与 Chat 独立建议卡；含并发确认在内的 16 项定向测试、JS 语法及 491 项角色门禁 PASS。建议不属于反馈确认摘要，不创建清洗管线；8081 旧进程未在本切片重启。
- 2026-10-24：ACCEPTED（已批准 VLM 检测器切片），任务卡 vlm-approved-detector.json，发布基线 7004ee9b0721ec7a355afd16a511311ad3d81e8f，A integration 独立 worktree。已实现任务需求/模型配置发布绑定、VLM 三态判定映射和失败关闭；模拟批准工作器三态链路测试 29 项定向 PASS，489 项角色门禁 PASS。此切片不自动批准 Chat 草稿，不代表真实检索/清洗端到端。
- 2026-10-23：ACCEPTED（模型稳定性切片），model-live-reliability.json / da5ee05ad8166dbb77d798591a5519a692ede618。百度图片真实测试 40 张（狗/猫各 20，JPEG 内容去重后 38 张）；gpt-4o-mini VLM 初轮狗图 12 pass/1 fail/1 unknown/6 error，猫图 0 pass/12 fail/0 unknown/8 error。抽查发现上游 429 限流，已加入最多 2 次有界重试；8 项定向测试、482 项角色门禁 PASS，6 张此前失败图片复测均得到判定，其中 1 张用到第二次请求。结果不是权威清洗计数；Chat 真实 LLM 生成两组待确认卡，未代用户确认。当前真实检索和清洗 worker 仍未接通。
- 2026-10-23：ACCEPTED（本地模型接入切片），model-selection.json / 30a5830b39f3c19e81195845354f7403e78dcafb。模型配置/脱敏目录/选择、LLM 扩词→Chat 草稿→确认历史及 VLM 后端判定接口已实现；21 项定向测试、480 项角色门禁及 8081 空配置页面冒烟 PASS。真实端点资料待用户提供；受信清洗 worker 装配未完成，不代表真实检索/清洗端到端完成。详见 integration 交接和 contracts/model-runtime.md。

## 多分支账本验收

- 2026-10-23：ACCEPTED（本地分页账本切片），core 多分支分页预留/回传已审查并合并；任务卡 multi-branch-pages.json，基线 73ab359f118af480174fb917c4a2af6c01f12ce7。16 项定向测试、474 项角色门禁 PASS；实现成员游标隔离、并发幂等、旧集合/generation 栅栏、在途上限和格式 1→2 原子迁移。明细见 core handoff；无额外重复全量回归。
- 下一步：冻结待执行记录读取与恢复契约，再接执行器；当前没有后台调度、真实检索或页面操作入口，本轮不修改 8080/8081 用户账本。

## 整组批准验收

- 2026-10-23：ACCEPTED（本地持久化切片），core 多分支整组批准持久化 b4f0e30 已审查并快进合并 master。put/confirm/get_current、整组 scope 重绑定、幂等/并发一次效果、失败回滚及独立进程恢复已验证；13 项定向测试、465 项 core 门禁 PASS。任务卡 multi-branch-storage.json，基线 cc9d14c6e3b5f27484ed47299babbfc6ba04fdc7；明细见 core handoff。未额外重复全量回归。
- 下一步：成员级分页预留/回传、分支游标隔离和旧集合回传栅栏；尚无真实检索、页面批准入口或后台调度。本轮未修改 8080/8081 服务及用户账本。

## 多分支契约验收

- 2026-10-23 17:34 CST：ACCEPTED（本地契约切片），multi-branch-contract.json / 9c94b84c2769c1ccbf3cde9d34038e6907acfb69；整组模型、确认摘要与成员执行校验、后续账本协议已实现，18 项定向及 458 项角色门禁 PASS。未实现整组持久化或调度，页面不变；下一包推进 core 原子批准与 SQLite 恢复验证。

## 物理准备验收

- 2026-10-23 15:25 CST：ACCEPTED（本地后端准备切片），physical-query-preparation.json / cb1388c3d0fd7a61ea0d5ad8902e24961c0f8608；授权目录选择、完整绑定能力注册、编码及权限/版本复验已实现，12 项定向测试、452 项角色门禁 PASS。无真实依赖调用，无自动批准或执行；8081 页面保持上一包。下一步需扩展多分支批准契约，不能把现有单当前计划账本当作多分支调度。

## 查询分支预览验收

- 2026-10-23 14:44 CST：ACCEPTED（逻辑预览切片），当前任务 query-branches.json，基线 80712ee58d6f556a1453533876b03113820eb60c。分组编辑 74c8121 已合并 master；查询分支编译、只读 API、Chat 入口已实现。20 项定向测试、445 项角色门禁、8081 页面验收 PASS，详见 integration 交接。
- 已获本地提交、任务基线发布及合并授权；不推送远端、不安装依赖、不代业务审批。当前提供逻辑查询预览，不是已执行检索；下一步接授权选库、编码能力校验及物理计划批准。真实模型、内网检索、分组配额与打包仍未接通。

## 前包验收快照

- 更新时间：2026-10-23 13:32 CST（Asia/Shanghai）
- 当前任务：`docs/dev/tasks/group-editor.json`；A integration / `codex/integration`；基线 `3db96536f6eb76bde6ceda95f0b97b01533f8695`。
- 状态：READY_FOR_REVIEW；可编辑主标签与召回词、返回修改确认卡、历史正式版本分组展示已实现，尚未发布到 8080 体验服务。
- 验证：最新 15 项定向测试 PASS；修复结束加载清空错误提示、确认成功后旧卡仍能返回编辑的问题，新增 JavaScript 行为测试。角色门禁 SCOPE PASS、440 项 PASS、LOCAL GATE PASS（退出码 0）；未在门禁外重复完整回归。
- 页面验收：隔离端口 8081 / 独立 SQLite 账本中，多行分组、零召回词、返回修改、确认及刷新读回分组 PASS；8080 用户账本未改动。
- 未接通：真实模型扩词、检索、分组配额及实际打包，不宣称全项目完成。
- 下一步：按规格书推进较大工作包“查询分支编译及预览”，保留原始查询、去重、词来源及约束引用；随后接现有批准账本与检索装配。不改写既有 RetrievalPlan 模型。发布任务基线、提交与本地合并等待明确授权，已提出询问。

## 前次切片记录

- 更新时间：2026-10-23 02:51 CST（Asia/Shanghai）
- 总控角色 / 分支：A integration / `codex/integration`
- 当前发布基线：`e2d625c`（主标签/辅助标签扩词分组预览已验收）
- 当前规范任务：`docs/dev/tasks/grouped-expansion-preview.json`
- 总体状态：ACCEPTED（主标签/辅助标签扩词分组预览）；不代表 v1.9 完整产品验收。
- 已完成：受信检测器发布配置加载、启动预检、制品与模型摘要、脱敏证据；清洗批准已绑定 `detector_release_digest`，core 账本持久化并兼容旧记录；运行时发布漂移会在素材读取前失败关闭。
- 正在进行：本切片已收口；下一阶段接用户可编辑分组、分组交付配额或真实扩词 provider。
- 当前阻塞：无。
- 下一步：增加用户可编辑分组和每组交付配额确认，再将已确认辅助词编译为真实查询分支。
- 本地验收：8080 输入“动物，狗扩20个词，猫扩30个词”后，页面显示狗 20 词/独立包、猫 30 词/独立包、两组配额“尚未指定”和跨组聚合只计一次；停留在确认卡，未代用户确认正式提单。
- 本轮验证：分组扩词/API/UI 定向 11 项 PASS，JavaScript 语法 PASS；integration 门禁 SCOPE PASS、436 项 PASS、LOCAL GATE PASS。未在门禁外额外运行完整回归。
- 本地验收：8080 页面显示“结果尚未发布”，并明确未发布当前版本权威结果集时不展示模拟素材或推算数量。
- 本轮验证：结果视图 HTTP/UI/model 定向 12 项 PASS，JavaScript 语法 PASS；integration 门禁 SCOPE PASS、431 项 PASS、LOCAL GATE PASS。首次定向运行因测试夹具写错 `ResultReader` 赋值位置失败，修正后真实通过；未在门禁外额外运行完整回归。
- 本地验收：8080 服务已从发布基线重启；浏览器确认正式任务显示 revision 1 / generation 1、阶段 PLANNING、目标 3、五个节点、有效数量与缺口“未知”，并显示“不展示估算百分比”。
- 本轮验证：进度 HTTP/UI/model 定向 11 项 PASS，JavaScript 语法 PASS；integration 门禁 SCOPE PASS、425 项 PASS、LOCAL GATE PASS。按用户要求未在门禁外重复完整回归。
- 本轮验证：HTTP/UI/core 定向 11 项 PASS，JavaScript 语法 PASS；integration 门禁 SCOPE PASS、420 项 PASS、LOCAL GATE PASS；浏览器本地合成会话/确认关联 PASS。未在门禁外重复完整回归。
- 本轮验证：`tests.test_conversation_contracts` 5 项 PASS；integration 门禁 SCOPE PASS、415 项 PASS、LOCAL GATE PASS。未在门禁外重复完整回归。
- 最近验证：历史 HTTP/UI/core 定向 10 项和 JavaScript 语法检查通过；integration 门禁 SCOPE PASS、完整 410 项通过、LOCAL GATE PASS；浏览器显示 2 条 SQLite 历史任务并成功打开 revision 1 任务继续反馈。完整 `conversation_message` 草稿历史、真实 W3/内网 NOT_RUN。

## 更新规则摘要

- 活跃开发至少每 30 分钟更新；开工、关键里程碑、阻塞/恢复和交付立即更新。
- A 更新本页；B/C/D 只更新各自 handoff，由 A 汇总。
- 无变化也记录原因；测试计划、RUNNING、PASS、FAIL、NOT_RUN 分开表述。
