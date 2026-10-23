# A 总控集成交接

## 可编辑交付分组（2026-10-23）

- 13:32 CST：确认成功后清空草稿引用并移除旧卡操作；确认失败保留草稿用于重试。确认/编辑入口增加状态防护，避免已确认方案退回草稿编辑。新增 Node 行为测试覆盖失败保留、成功移除、重复调用不再请求；同一定向命令 15 项 PASS，同一角色门禁 440 项 PASS、SCOPE PASS、LOCAL GATE PASS（退出码 0），git diff --check PASS。8081 隔离服务已重启加载变更；本次状态防护以行为测试验证，未重复浏览器合成提单。8080 未变，提交与合并仍未执行。
- A integration；独立 worktree 为本文件所在仓库；分支 codex/integration；基线 3db96536f6eb76bde6ceda95f0b97b01533f8695；任务卡 docs/dev/tasks/group-editor.json。
- READY_FOR_REVIEW：结构化输入主标签/召回词，返回修改后重新生成确认卡；历史正式版本展示保存的分组。新 generator=user-edited@1 允许零辅助词；旧 local-template@1 保持兼容。词来源不是素材分类证据。
- 变更影响 API/公共分组验证/Chat；不启用配额计数或真实检索。后续阶段依次推进语义扩词 provider、查询分支编译与人工批准、真实检索装配、分组判定及数量分配、交付打包。
- 定向测试：PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_grouped_expansion tests.test_api tests.test_chat_ui -q，13 项 PASS；包括改词摘要变化及 SQLite 重启恢复。JavaScript node --check PASS。
- 角色门禁：`python3 tools/collab_gate.py check --role integration --base 3db96536f6eb76bde6ceda95f0b97b01533f8695 --task docs/dev/tasks/group-editor.json`：SCOPE PASS、438 项 PASS、LOCAL GATE PASS，退出码 0；其中 LOCAL GATE FAIL 是既有负向测试输出。未在门禁外重复完整回归。
- HTTP 冒烟：复用 ApiTest 本地临时服务器，结构化狗/猫组及零辅助词返回 200，缺少身份返回 401，越项目返回 403，全部断言 PASS。
- 2026-10-23 13:30 CST 续开发：浏览器首次因启动遗漏 --demo-project 返回 403，暴露 setBusy(false) 清除错误提示的问题；补齐独立预览服务参数，并修复 UI 保留失败提示。新增 Node 行为测试验证多行/零召回词解析及错误提示，不仅检查语法。
- 最新定向命令同上，14 项 PASS；同一基线角色门禁 SCOPE PASS、439 项 PASS、LOCAL GATE PASS，退出码 0。没有在门禁外重复完整回归。
- 浏览器验收 PASS：8081 使用独立 var/integration/group-editor-acceptance.sqlite3，输入狗（金毛、柯基）和猫（零词），返回修改为狗（柯基）/猫（橘猫）；确认本地合成任务后刷新，历史正式版本保留两组及用户编辑来源。未操作 8080 用户账本。当前服务进程用于隔离体验，不代表发布。
- 交付状态：改动保留在 integration 工作区，尚未提交或合并；不访问内网、真实素材或真实模型。
- 后续大包提案（规格 §5.3/§12.4）：统一完成查询分支编译、确定性指纹去重、原始核心查询保底、group/source_term 来源、constraint_refs 保留、预览 API/UI 与负向测试；只生成提案，不自行批准或触发检索。复用现有 RetrievalPlan/批准账本，执行装配涉及 core 文件时另行分派，不跨角色修改。发布新任务基线与本地提交/合并已向用户申请授权，未获答复前不自行执行。

## 主标签/辅助标签扩词分组预览（2026-10-23）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；任务卡 `docs/dev/tasks/grouped-expansion-preview.json`，可信任务基线 `3fc0eaae5d054e3f63a025ebddc45fc8767ccfa6`，任务起点 `56882a763d2ae72af4a28505f21e677f7d7e639b`；状态 ACCEPTED（本地体验切片）。
- 新增 `grouped-expansion@1` 和项目级扩词预览路由。只识别明确的“主标签扩N个词”（1–30），最多 20 组；无明确语法返回未识别，不猜测分类。当前 `local-template@1` 是确定性、可审阅模板，不宣称模型能力。
- 主标签固定 `package_mode=separate` 并独立命名交付包；辅助词只用于召回，不成为包或配额。`delivery_target=null` 防止把扩词数量误当成交付图片数；跨组聚合默认同一素材只计一次。
- Chat 示例 `动物，狗扩20个词，猫扩30个词` 会展示上级标签、每组完整辅助词、独立打包、尚未指定配额及跨组计数规则；确认草稿保存完整计划和摘要，篡改失败关闭。
- 定向命令 `PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_grouped_expansion tests.test_api tests.test_chat_ui -q`：11 项 PASS；JavaScript `node --check` PASS；integration 门禁 SCOPE PASS、436 项 PASS、LOCAL GATE PASS。未在门禁外额外运行完整回归。
- 发布提交 `e2d625c` 已快进到主体验目录，8080 服务使用既有 SQLite 账本重启。浏览器验收输入“动物，狗扩20个词，猫扩30个词”：狗组完整显示 20 个辅助词和独立包，猫组完整显示 30 个辅助词和独立包；两组交付配额均为“尚未指定”，确认卡显示跨组聚合只计一次。验收停留在确认卡，没有代用户确认正式提单；产生 1 条本地草稿消息。
- 边界：不调用模型、不生成真实查询计划、不启动检索/清洗/补数/打包；不访问内网或真实数据。后续需增加用户可编辑分组、每组交付配额和真实扩词 provider。

## 权威结果统计与稳定分页预览（2026-10-23）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；任务卡 `docs/dev/tasks/result-preview-ui.json`，可信任务基线 `9c9ddb8105b2fc2f8e11b122145526a64f3d9bc4`，任务起点 `4aa8ba9f3b3a7286e474d4fca301773d61e1fb2d`；状态 ACCEPTED（本地体验切片）。
- 新增当前结果只读视图与 `GET /v1/retrieval/tasks/{task_id}/results`。无权威发布时明确返回 `available=false`、结果统计 null 和空 items；只把结果仓库的 NOT_FOUND 转为空态，撤权、旧版本和存储错误不吞掉。
- 已发布结果必须绑定当前 generation/task_version/目标数量，accepted/reserve 独立分页；游标绑定结果摘要、视图和偏移。条目仅返回逻辑素材标识、版本及 `preview_status=unavailable`，不暴露摘要、路径、私有地址或图片数据。
- Chat 正式任务页增加“结果尚未发布”空态；存在权威结果时才展示真实统计和分页入口。本地 CLI 尚未装配执行链结果仓库，测试读取器只验证契约形状，不代表真实检索/DJ 联测。
- 验证状态：`PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_result_views tests.test_api tests.test_chat_ui -q` 12 项 PASS；JavaScript `node --check` PASS；integration 门禁 SCOPE PASS、431 项 PASS、LOCAL GATE PASS。首次定向运行因测试夹具把值写到 reader 对象而非其 items 映射而失败，修正后真实通过；未在门禁外额外运行完整回归。
- 发布提交 `7f45be0` 已快进到主体验目录，8080 服务已使用既有 SQLite 账本重启。浏览器确认当前正式任务显示“结果尚未发布”及“不展示模拟素材或推算数量”，旧历史、五节点和反馈入口保持可用。
- 边界：本切片不启动检索/清洗/补数，不发布结果集，不实现媒体预览、SSE、逐步骤结果或人工复核，不访问内网/真实数据。

## 五节点只读进度快照与页面（2026-10-23）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；任务卡 `docs/dev/tasks/task-progress-ui.json`，可信任务基线 `e0dd3322676245d1ad7f505fb50aa42b7738a1f5`，任务起点 `3967615f90e884edebda3512733e1d5a4aaf31dc`；状态 ACCEPTED（本地体验切片）。
- 新增五节点只读投影与 `GET /v1/retrieval/tasks/{task_id}/progress`。快照绑定 task/revision/task_version/generation，显示确认方案中的 manual/auto；只根据现有 run_status/stage 和已进入后继阶段这一事实推进节点，未知 stage 不猜测完成状态。
- 目标数量来自已确认方案；没有权威结果集时 returned/passed/duplicate/accepted/pending_review/gap 全部为 null，页面显示“未知”，不计算整体百分比。当前本地 `PLANNING` 任务仅把扩词/选库/计划节点显示为 running，不声称检索或清洗已启动。
- 定向命令 `PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_progress_models tests.test_api tests.test_chat_ui -q`：11 项通过、退出码 0；`PYTHONPATH=src python3 ... | node --check`：退出码 0；integration 门禁：SCOPE PASS、425 项通过、LOCAL GATE PASS。按用户要求不额外重复完整回归。
- 发布提交 `8c4024e` 已快进到主体验目录，8080 服务已使用既有 SQLite 账本重启。浏览器验收：任务 `rt_35a5555bb7354314acddeccee237916b` 显示 revision 1 / generation 1、阶段 PLANNING、目标 3；五个节点齐全，仅扩词节点为 running，其余 pending；有效数量与缺口为“未知”，页面明确不展示估算百分比。
- 边界：即时只读投影，不是持久 workflow_node_instance 或事件历史；没有统计水位、结果分页、SSE、节点批准、控制命令或后台执行，不访问内网/真实数据/DJ。

## 草稿会话历史 HTTP/UI（2026-10-23）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；任务卡 `docs/dev/tasks/conversation-history-ui.json`，可信任务基线 `8805aff4ae7bb73d84dd629d926b210604d92941`，任务起点 `b3b13c6b98964402df9ed4e43eebf7a1259c8557`；状态 ACCEPTED（本地体验切片）。
- 新增当前项目会话列表和单会话读取 GET 路由，均要求可信身份并通过服务层创建人/项目权限复验。历史侧栏分为“草稿会话”和“正式需求”；会话详情按序展示需求/反馈及独立“方案已确认”关联，可跳转正式 revision，且明确会话不是业务执行时间线。
- 旧 SQLite 账本通过 core 迁移保持可读；功能上线前未记录的消息不会伪造补齐，因此旧任务仍只出现在“正式需求”。公开消息不含 tenant/owner，消息本身不携带 task/revision。
- `PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_api tests.test_chat_ui tests.core.test_conversation_storage -q`：11 项通过、退出码 0；`PYTHONPATH=src python3 ... | node --check`：退出码 0。首次 JS 管道提取遗漏 `PYTHONPATH` 产生 `ModuleNotFoundError`，已修正后真实通过。`python3 tools/collab_gate.py check --role integration --base 8805aff4ae7bb73d84dd629d926b210604d92941 --task docs/dev/tasks/conversation-history-ui.json`：SCOPE PASS、420 项通过、LOCAL GATE PASS、退出码 0；其中 `LOCAL GATE FAIL` 是既有负向夹具输出。按用户要求不额外重复完整回归。
- 本地 8080 服务已用主账本重启。浏览器验证：旧账本显示“暂无已记录会话；旧账本无法补回此前聊天”且原 2 条正式任务保留；新建“会话历史体验验证：找 3 张明亮书房图片”后侧栏显示 1 条输入/0 次确认，确认后显示 1 次正式确认及 revision 1；打开会话可见原始输入、独立边界说明、任务 ID 与“查看正式版本”入口。验证产生 1 条本地合成正式任务，未触发检索或清洗。
- 边界：本地模拟身份/SQLite 体验；没有附件、assistant 消息、保留清理、全文搜索、W3/内网/真实数据或 DJ。

## 独立草稿会话记录公共契约（2026-10-23）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；任务卡 `docs/dev/tasks/conversation-message-contract.json`，可信任务基线 `89b733e6abcc2f463e1d5be62537d02547157489`，任务起点 `50c067ac39b608cbb1ce63118eabfdd1d006289f`；状态 READY_FOR_REVIEW。
- 新增 `ConversationMessage`、`ConversationConfirmation`、`ConversationSnapshot`：消息只绑定会话草稿，正式 task/revision 只出现在独立确认关联；公开消息视图不返回 tenant/owner，也不携带正式执行身份。消息序号、类型、正文长度、时区时间和跨会话/项目绑定严格校验。
- `Draft`、`Task` 增加末尾可选 `conversation_id`，旧构造保持兼容；`TaskRepository` 冻结草稿/反馈消息同事务保存和会话读取端口。执行仍只读取已确认不可变方案，消息缺失或删除不改变正式方案。
- 定向命令 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_conversation_contracts -q`：5 项通过、退出码 0。`python3 tools/collab_gate.py check --role integration --base 89b733e6abcc2f463e1d5be62537d02547157489 --task docs/dev/tasks/conversation-message-contract.json`：SCOPE PASS、415 项通过、LOCAL GATE PASS、退出码 0；其中 `LOCAL GATE FAIL` 是既有负向夹具输出。按用户要求不在门禁外重复完整回归。
- 边界：本切片不实现持久化、服务/API/UI、assistant 消息、附件或保留清理；未访问内网、真实业务数据、W3 或 DJ。下一步发布契约基线后，由 core 在独立任务中实现 SQLite 原子持久化与旧账本迁移。

## 检测器发布身份与脱敏启动证据（2026-10-22）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；任务卡 `docs/dev/tasks/detector-release-evidence.json`，可信发布基线 `6f1cf6e60a1fa933713fb927c82b709558687d51`，任务起点 `c1cb598ab70446c106e5f18f460790ebaa642d08`；状态 READY_FOR_REVIEW。
- 扩展 `detector_artifacts.py`、`released_detectors.py` 及两组既有测试，更新契约和本交接。`manifest_digest` 规范化覆盖后端元数据与实现/模型制品角色、相对路径、SHA-256；配置顺序和部署绝对根目录不影响身份。`release_digest` 组合能力目录摘要与制品清单摘要，任一能力或制品绑定变化均产生新身份。
- `preflight_evidence()` 每次重新执行全量启动预检，成功时只返回 schema、组合/组成摘要及检测器/制品数量，不返回部署路径、单文件摘要或运行时对象；文件篡改、缺失、绑定或目录漂移不产生成功证据。证据/摘要不是授权、签名或模型质量结论，批准账本尚未持久绑定它。
- 定向命令 `PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_detector_artifacts tests.test_detector_release_config tests.test_released_detectors tests.test_multi_rule_pipeline -q`：30 项通过、退出码 0。按用户要求未单独重复完整回归。`python3 tools/collab_gate.py check --role integration --base 6f1cf6e60a1fa933713fb927c82b709558687d51 --task docs/dev/tasks/detector-release-evidence.json`：SCOPE PASS、门禁内 380 项回归通过、LOCAL GATE PASS、退出码 0；输出中的 LOCAL GATE FAIL 来自既有负例夹具。此后只更新本条证据并执行 `git diff --check`，未为纯交接文本再次重复回归。
- 边界：仅本地合成配置/制品/检测器；未注册真实模型、运行真实 DJ、访问内网、安装依赖或推送。下一步如将 release_digest 纳入正式批准/作业绑定，将涉及公共契约和 core 持久化，必须单独任务卡与迁移验收。

## 受信检测器发布配置加载与启动预检（2026-10-22）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；任务卡 `docs/dev/tasks/detector-release-config.json`，可信发布基线 `285e36d1e0900702f509675769f9b69130ad6148`，任务起点 `4963dd7cea4b466193eb4afe239186bd60854fec`；状态 READY_FOR_REVIEW。
- 新增 `src/dsh_retrieval/detector_release_config.py`、`tests/test_detector_release_config.py`，扩展 `detector_artifacts.py`、`released_detectors.py`、契约和本交接。服务端以显式配置路径和独立部署根目录构造 registry；JSON 不得携带根目录，不接受请求覆盖，不动态导入、安装或下载。
- 严格加载拒绝未知/重复字段、错误 schema、空/超限目录、重复后端/制品、绝对/越界/超长路径、非法摘要、非普通/符号链接配置及超过 1 MiB 的文件。启动预检要求配置后端、运行时检测器和已发布 DetectorBinding 的 backend 全集一致，并校验 callable、元数据、绑定摘要及全部实现/模型文件；启动后每次未完成执行仍沿用原制品复核。
- 定向命令 `PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_detector_release_config tests.test_detector_artifacts tests.test_released_detectors tests.test_multi_rule_pipeline -q`：27 项通过、退出码 0。完整命令 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`：377 项通过、退出码 0（370 基线 + 7 新增）。`python3 tools/collab_gate.py check --role integration --base 285e36d1e0900702f509675769f9b69130ad6148 --task docs/dev/tasks/detector-release-config.json`：SCOPE PASS、377 项通过、LOCAL GATE PASS、退出码 0；输出中的 LOCAL GATE FAIL 来自既有负例夹具，不是本次门禁失败。
- 边界：仅本地合成 JSON、实现字节、模型字节和检测器；没有真实模型注册/推理、真实 DJ、内网、安装、下载或推送。启动预检证明配置和本地制品一致，不证明模型质量、许可证、性能、资源策略或生产可用性；未新增 HTTP/CLI 入口。

## 检测器实现与模型制品 SHA-256 绑定（2026-10-22）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；任务卡 `docs/dev/tasks/detector-artifact-binding.json`，可信发布基线 `3ca43f8754ecf718c978706c652f12466c5d4ca1`，任务起点 `aa950bc2cd5022637c3beda03066ff5c02c0c907`；状态 READY_FOR_REVIEW。
- 新增 `src/dsh_retrieval/detector_artifacts.py`、`tests/test_detector_artifacts.py`，调整 `released_detectors.py`、既有发布目录/串联测试、`contracts/multi-rule-cleaning.md` 与本交接。服务端受信清单按 backend_ref 绑定 validator/evidence、实际实现文件及可选模型文件的相对路径和 SHA-256；运行时检测器须声明同一绑定摘要。
- 每次未完成执行或预览续发前复核固定部署根目录身份、普通文件类型、路径边界、符号链接、文件摘要、检测器元数据和绑定摘要。缺失、内容改变、运行时错配或部署根目录被替换均返回 `DETECTOR_NOT_RELEASED`，且发生在候选媒体读取前。已发布预览的重复读取仍走现有业务鉴权；已批准作业、当前权限、版本/generation、逐结果发布与预览幂等栅栏未改。业务调用只传 operation，不能提交制品根目录、路径或摘要。
- 定向命令 `PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_multi_rule_pipeline tests.test_released_detectors tests.test_detector_artifacts -q`：20 项通过、退出码 0。覆盖实现+模型成功、缺失、篡改、绑定错配、目录替换、符号链接，以及结果已落账但预览未发布时制品改变仍阻断且不重读媒体。
- 完整命令 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`：370 项通过、退出码 0（364 基线 + 6 新增）。`python3 tools/collab_gate.py check --role integration --base 3ca43f8754ecf718c978706c652f12466c5d4ca1 --task docs/dev/tasks/detector-artifact-binding.json`：SCOPE PASS、370 项完整回归通过、LOCAL GATE PASS、退出码 0。提交前 `master` 新增仅调整测试分层约束的 `979c28738c0c6d01ce79b50fe244bd36391fafe9`，任务提交线性同步该提交后，以它为最新主干基线重跑同一门禁：SCOPE PASS、370 项通过、LOCAL GATE PASS、退出码 0。输出中的 LOCAL GATE FAIL 来自既有门禁负例夹具，不是本次门禁失败。
- 边界：全部为临时目录内的合成实现字节、合成模型字节和合成检测器；没有登记或调用真实模型，没有运行真实 Data-Juicer，没有访问内网、安装依赖、推送或更改用户运行数据。SHA-256 一致仅证明本地发布文件与受信清单一致，不证明模型质量、许可证、资源策略、在线性能或规格书 7.8/15.6/17.4 的完整发布验收。

## 已发布检测能力执行前门禁（2026-10-22）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；任务卡 `docs/dev/tasks/released-detector-gate.json`，发布基线 `0da4e22de1f5300dcd18df6b0f55980e94d46c26`；状态 READY_FOR_REVIEW。
- 新增 `src/dsh_retrieval/released_detectors.py`、`tests/test_released_detectors.py`，调整 `src/dsh_retrieval/multi_rule_pipeline.py`、`tests/test_multi_rule_pipeline.py`、`contracts/multi-rule-cleaning.md` 与本交接。受信配置的能力目录摘要须与不可变配方相同；硬规则逐条要求已验证能力、媒体/谓词/操作符/值类型和检测步骤/实际后端一致。无匹配、目录改变或未发布能力在读取媒体前失败关闭；结果已落账但预览未发布时仍重验当前目录。
- 定向 `PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_multi_rule_pipeline tests.test_released_detectors -q`：14 项通过；完整 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`：364 项通过。`python3 tools/collab_gate.py check --role integration --base 0da4e22de1f5300dcd18df6b0f55980e94d46c26 --task docs/dev/tasks/released-detector-gate.json` 退出码 0，SCOPE PASS / LOCAL GATE PASS，364 项通过；输出中的 LOCAL GATE FAIL 是门禁负例夹具。验证目录变更、人工/不可用能力、类型/操作符/后端漂移、无规则的额外步骤及旧串联回归。均为本地合成检测器。
- 边界：目录由服务端受信部署配置构造，不从请求体接受；本门禁尚不能证明实际实现文件/权重摘要、模型评测、资源策略或运行质量。未登记真实 OCR/水印模型，未运行真实 DJ/内网；不把此切片称为真实多规则清洗。

## 已批准多规则作业的本地串联（2026-10-22）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；任务卡 `docs/dev/tasks/multi-rule-pipeline.json`，发布基线 `8bb0f576d0676ed26d35e6e403ef25e5fbb59e81`；状态 READY_FOR_REVIEW。
- 新增 `src/dsh_retrieval/multi_rule_pipeline.py`、`tests/test_multi_rule_pipeline.py` 与本交接。入口只按 operation_id 从受信账本读取当前批准作业，使用现有 `ApprovedManifestAccess`、`GenericCleaningExecutor`、`publish_result` 与 `publish_preview`；不接受客户端自建作业或媒体路径。
- 幂等恢复：完整预览已发布时重新鉴权返回当前预览；逐素材结果已落账但预览尚未发布时直接续发，不再读取媒体或重跑检测。执行中撤权、旧 generation、缺登记检测器均失败关闭；结果/预览的原子性分别由 core 账本保证，跨两次事务的中断通过重试恢复。
- 验收：`PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_multi_rule_pipeline -q` 7 项通过；完整 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q` 357 项通过。`python3 tools/collab_gate.py check --role integration --base 8bb0f576d0676ed26d35e6e403ef25e5fbb59e81 --task docs/dev/tasks/multi-rule-pipeline.json` 退出码 0，SCOPE PASS / LOCAL GATE PASS，357 项通过；输出中的 LOCAL GATE FAIL 是已有门禁负例。仅合成字节和合成检测器，SIMULATED。
- 边界：同步本地装配示例，尚无队列派发、租约/超时/重试预算或真实检测器注册；不运行真实 DJ、多规则质量评测、内网服务、补数或最终 delivery。任务预览不等于 v1.9 完整交付。

## input_only 精确结果预览契约（2026-10-22）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；任务卡 `docs/dev/tasks/exact-result-set-contract.json`，发布基线 `990cb523e93ce7500c604554d405d92003203d25`；状态 READY_FOR_REVIEW。
- 修改 `src/dsh_retrieval/result_contracts.py`、`tests/test_result_contracts.py`、`contracts/result-set.md` 与本交接。输入只接受完整且绑定批准作业的逐素材结果，限定 `image` 固定输入；全规则 pass 后按内容摘要精确去重、稳定选代表，目标内 accepted，余量 reserve，fail/unknown/重复分别计数。缺口与停止原因一致，输出摘要绑定 operation/代次/版本/清洗结果。
- 验收：合成契约测试覆盖目标达成/不足、重复代表、非通过排除、旧绑定/缺候选/非法目标拒绝、摘要稳定性。`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_result_contracts -q`：5 项通过；完整 unittest 344 项通过。`python3 tools/collab_gate.py check --role integration --base 990cb523e93ce7500c604554d405d92003203d25 --task docs/dev/tasks/exact-result-set-contract.json`：SCOPE PASS / LOCAL GATE PASS，344 项通过。测试输出中的 LOCAL GATE FAIL 来自既有门禁负例，不是本轮门禁失败。
- 边界：纯函数预览，不鉴权、不读取当前任务、不落账、不计算跨轮/近重复、不执行偏好排序、不派发补数；`SEARCH_STRATEGIES_EXHAUSTED` 只指本次固定输入用尽。不得作为正式 accepted_count 或交付快照。真实 DJ 多规则与内网未验证。

## 逐素材多规则清洗结果契约（2026-10-22）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；任务卡 `docs/dev/tasks/cleaning-result-contract.json`，受信执行基线 `e7ca2224a00ed4b32416677ce3ae70b3fd72d667`；状态 READY_FOR_REVIEW。
- 修改 `multi_rule_contracts.py`、`test_multi_rule_contracts.py`、`contracts/multi-rule-cleaning.md` 及本交接。新增共享 `CleaningRuleVerdict`/`CleaningCandidateVerdict`/`CleaningJobResult` 和 `require_complete_cleaning_result`，绑定已批准 operation/task version/revision/generation/清单/配方，要求候选与规则完整、唯一、顺序一致，三态汇总及证据语义一致。
- 定向契约测试 8 项通过（原 5 + 新增 3）；完整回归 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q` 退出码 0，333 项通过。`python3 tools/collab_gate.py check --role integration --base e7ca2224a00ed4b32416677ce3ae70b3fd72d667 --task docs/dev/tasks/cleaning-result-contract.json` 退出码 0，SCOPE PASS / LOCAL GATE PASS / 333 项回归通过。输出中 `LOCAL GATE FAIL` 来自已有门禁负例夹具，不是本次回归失败。
- 契约本身不代表当前授权、不持久化、不计算 accepted_count 或触发补数。NOT_RUN：真实 DJ 多规则、生产账本、ClickHouse/Milvus/W3、质量与性能评测。下游 cleaning 需迁移执行器输出，core 需做发布时原子 fencing 与幂等持久化。

## 开放需求理解审阅层（2026-10-22）

- A / codex/integration / var/worktrees/integration；基线 49d2f9c20420cca4f8ee8a256ec3b11a77628f61；任务卡 docs/dev/intent-review-task.md，契约 contracts/intent-review.md。范围不涉及角色私有目录或旧草稿 schema 变更。
- 新增 intent_review.py 与显式 CLI：原文/摘要/字符片段绑定、遗漏补 unresolved、硬软区分、同谓词 eq 直接冲突提示、受信注册能力检查、审阅摘要；始终不可执行、无业务任务副作用。未知新规则保留而非丢弃。
- 能力目录默认空；未接 LLM，不使用固定关键词冒充泛化理解。验证器/证据引用检查是配置完整性，不代表自动核实检测质量。现有 task creation 未接本模块，不能宣称第一阶段全完成或已经实现持久澄清循环。
- 实际完整回归 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`：249 项通过（232+17），退出码 0；核心用例包括否定原文保留、遗漏片段、来源/风格分离、未知/人工/未发布能力、参数类型、原文篡改、模型注入执行字段、无模型 CLI。
- `python3 tools/collab_gate.py check --role core --base 49d2f9c20420cca4f8ee8a256ec3b11a77628f61` 退出码 1，A 分支不匹配；无 A 专属角色门禁，不绕过/不称通过。未联网、运行 DJ、安装依赖或推送。
- 下一步：选择受控需求解析模型（已向用户询问部署方向），新增适配与真实语义评测；先保留所有未解析条件，不能因模型未配置让旧链路把复杂需求当成宽度通过。存储/确认门禁衔接仍需后续任务卡。

## 持久清单去重预览装配（2026-10-22）

- 14:10（Asia/Shanghai）真实合成联测 PASS：运行既有 `tools/manifest_cleaning_smoke.py --run-real`（DJ Python/source 参数同下方 M19 记录），4 个候选、3 pass/1 fail，unique_pass_count=2、duplicate_count=1、target=10、preview_gap=8；accepted_count=null、refill_dispatched=false，任务 RUNNING。清单四来源保留，重建仓库后预览一致。
- 证据原件 `/tmp/dsm-nn0ldlna/manifest-cleaning-report.json`，本地副本 `var/status/evidence/2026-10-22-manifest-dedup.json`；operation clean_1b5d125e8241484b87591dee63c10aca，DJ task_e40513180b69/run_r001。仅真实 DJ 固定宽度规则；检索/权限是模拟，不是内网或全规则合格集。
- 主分支合入 core 后 232 项回归通过，integration smoke 扩展后完整 unittest 同样 232 项通过、git diff --check 通过。A 本轮执行 core 门禁（base=eac0b97f1eb939686a776d7d749105445cbc7c53）因 A 分支不匹配退出码 1；core 实现的角色检查真实通过，不混淆二者。
- 下一步正式结果版本/原子发布契约：当前 preview_only=true，不将宽度单规则预览发布为“满足全部需求”的交付；需要明确支持规则与 unknown 边界。

- A / codex/integration / var/worktrees/integration；起点 eac0b97f1eb939686a776d7d749105445cbc7c53。范围 tools/manifest_cleaning_smoke.py、本交接、生成报告；验收 v1.9 13.5/13.5.1、A20/A36/A50 的固定宽度/单清洗作业子集。
- core 以 1c89f59f00707d739e1731c4bf7334fc0bed25d1 为基线，角色门禁与 232 项测试通过；A 审查二次授权和不写任务状态路径后集成 eac0b97。无需新增接口：原计数器可消费逻辑候选，前轮“必须调整路径契约”的假设由本轮代码检查/测试修正。
- 本轮 smoke 扩展验收：4 张合成 PNG，经真实 DJ 3 pass/1 fail，再得 2 个唯一 pass 代表、1 个重复；清单四个来源仍保留，accepted_count=null、refill_dispatched=false、任务 RUNNING。允许受控既有真实 DJ 环境，使用唯一短路径 workspace；不安装、改上游、推送或接内网。

## 持久清单真实 DJ 合成联测（2026-10-22 12:32，Asia/Shanghai）

- 集成结果：4f8e732a18de34d963c5b9fb73823e9173ed34b6 已快进合入主分支；主工作区完整 unittest 回归 224 项通过、退出码 0。本节的真实运行在独立 integration workspace 执行；主分支没有重复启动 DJ。

- A / codex/integration / var/worktrees/integration；基线 084c65b80d8cae52fc096604b4adf3bfb3724a36；范围/验收见 docs/dev/manifest-real-smoke-task.md。新增可重复真实 smoke 入口和两项安全入口测试；DJ 子进程显式独立 DSH_HOME。
- 实际命令：`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 tools/manifest_cleaning_smoke.py --run-real --dj-python /Users/hw-edit/Documents/ChatGPT/search-agent/var/dj-runtime/bin/python --dj-source /Users/hw-edit/Documents/ChatGPT/search-agent/data-juicer-codex-dj-plan-explorer`。只读使用已有解释器/源码，无安装；运行、缓存、DSH_HOME 独立，不共享 DJ 可变 workspace。
- 首次失败：macOS 默认临时路径导致 DJ multiprocessing `OSError: AF_UNIX path too long`；证据 `/var/folders/sn/hl5d_x_d6f951djxc0rdlx800000gn/T/dsm-r9kzt618/manifest-cleaning-report.json`，stderr 位于该目录 `.dj/tasks/task_07a909a575f3/runs/run_r001/logs/stderr.log`。改 smoke 使用唯一 /tmp 短路径，未改 vendored DJ。
- 重试退出码 0，REAL_DJ_SYNTHETIC_MEDIA PASS：4 张程序生成 PNG，pass=3/fail=1/unknown=0；operation=clean_c87aa1620e6c49ffb1493d9db4d2bc0f，DJ task_c38950ebba69/run_r001。新下载路径重新 submit 同已批准 operation，仍同 backend；临时下载目录为空；SQLite 操作只有逻辑输入、不留临时路径；重建仓库后 start/poll 幂等。
- 原始证据 `/tmp/dsm-bgs9ukcy/manifest-cleaning-report.json`，本地报告副本 `var/status/evidence/2026-10-22-manifest-cleaning.json`（Git 忽略）；临时 workspace 日后可能被清理。accepted_count=null、任务仍 RUNNING；没有把 3 个 pass 当去重交付数。检索与授权为模拟夹具，不能称真实语义检索/内网验收。
- 完整 unittest 命令本轮 224 项通过（222+2）、退出码 0；git diff --check 通过。cleaning 角色检查基线同上，退出码 1（A 分支不匹配）；无 A 专用门禁，不声称通过角色范围检查。
- 下一步：无路径候选去重与正式结果契约；全规则、生产恢复、运行中断电、内网仍未验证。未推送、部署或修改用户业务数据。

## 已发布候选接入清洗（2026-10-22 12:24，Asia/Shanghai）

- 集成更新：实现 0bb335942f43a48e045f4308f2400caa0e80e7c6 已快进合入 master；主工作区重跑完整 unittest 命令，222 项通过、退出码 0。ACCEPTED 仅本地模拟装配切片，真实 DJ 新模式仍 NOT_RUN。

- A / codex/integration / var/worktrees/integration；基线 59ce9e8ead931bbe68959998238c0a788de8f8e2，范围/验收见 docs/dev/manifest-cleaning-task.md。仅修改 A 文件，没有接管 core/retrieval/cleaning 角色目录。
- 新增 manifest_cleaning.py：绑定 actor/task 的 PublishedManifestCatalog 和 ManifestCleaningExecutor；提案 source_path 为空占位，提交时短期下载验证，后端须明确声明同步冻结输入及路径无关幂等。既有 CleaningService 仍负责单独确认、CAS、回传与幂等；增加提交后/轮询/终态结果的清单权限复查。
- DJCleaningExecutor 新增显式 ephemeral_inputs=False 默认参数；启用后 bridge 指纹忽略临时路径而覆盖身份/摘要/配方/binding，旧模式指纹不变。poll/cancel 不依赖临时源路径；后端自身冻结副本不由本下载目录清理删除。
- 修改文件：src/dsh_retrieval/manifest_cleaning.py、cleaning_service.py、adapters/dj_cleaning.py、tools/dj_cleaning_bridge.py、tests/test_manifest_cleaning.py、本交接及任务卡。
- 实际命令 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`：222 项通过（204 原基线 + 18），退出码 0；git diff --check 通过。`python3 tools/collab_gate.py check --role cleaning --base 59ce9e8ead931bbe68959998238c0a788de8f8e2` 退出码 1，原因 A 分支不是 cleaning 分支；没有 A 专用门禁，不改检查器绕过，不声称角色检查通过。
- 模拟验收：清单发布→清洗提案→显式确认→逐条回传、重复确认不重提、失败后新目录同 operation 重试、字节变化/超限/撤权/新 generation 阻断、提交后撤权取消、终态结果重鉴权、空清单/任意配方拒绝、独立进程读无临时路径操作。bridge 只验证默认/新指纹和 mock 调用参数，未启动真实 DJ。
- NOT_RUN：真实 DJ 新接入模式、真实图片解码、内网、强杀中间复制恢复、生产权限原子性/任务调度。不是 v1.9 全规则或 accepted_count 验收；未安装依赖/推送/部署。下一步补本链路合成图片真实 DJ 验证（单独受控运行），并衔接去重/正式结果；已有 counting 对源路径的依赖需先调整契约，不能直接用空路径计数。

## 候选清单账本集成验收（2026-10-22 12:16，Asia/Shanghai）

- 契约基线 8d7dcc8b39a672ad174cf91704a84e5c66eec883；core 实现 7b9c96d763a7581a39d495ee31f6181c95ac9ba6，角色范围与 204 项测试通过；A 审查事务、稳定 scope 读取例外、来源覆盖、权限重查和模拟服务组合后快进集成。
- 主分支合入后 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q` 退出码 0，204 项通过（178 原基线 + 6 契约 + 20 存储/模拟集成）。
- ACCEPTED：本地单页不可变候选清单发布切片。独立进程读取、幂等/并发单次确认、原子本地事件、失败回滚通过；不推进任务或计算 accepted_count，不派发清洗。媒体路径不落账本，存版本化引用不等于永久可下载。
- 当前仅合成字节/模拟网关/SQLite；真实 DJ、真实语义检索、内网联测均 NOT_RUN。没有新增依赖、推送或部署；未触碰用户运行库。新表不能由旧二进制直接读取，生产迁移/回滚未验收。
- 下一步：将已发布 manifest 重新鉴权/物化，并与清洗执行 context 生命周期衔接；不得把本提案确认当成清洗授权，不得放宽检索原 task_version 栅栏。跨页候选池、全量规则和生产持久化仍未完成。

## 2026-10-22 候选清单持久化契约

- A / codex/integration / var/worktrees/integration，基线 83b3607a96603e7e6ba18adbd62e1d57f24ed9f8；范围/验收见 candidate-ledger-task.md。
- 接口变更原因：临时路径不能充当清洗持久输入；新 ManifestItem/Proposal/Manifest 和 CandidateManifestRepository 保存引用与完整命中，CandidatePublicationService 在确认前重物化。影响 core 存储和未来 cleaning 装配；旧 CandidateIntake/清洗契约不变。
- 请求示例：preview(actor, task_id, operation_id) 返回 proposal_digest/expected_task_version/candidate_count；confirm 使用相同作业、显示的摘要与版本、key、action=confirm，返回 manifest_ref 与 replayed。身份全部后端绑定，不支持客户端传文件路径。
- 清单读取允许同稳定 scope/current plan 下 task_version 递增；新发布依旧严格版本核对。不改变检索读取栅栏；确认工件不增加 task_version、不代表批准清洗或通过规则。
- 本轮完整回归 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`：184 项通过（178+6），退出码 0。`python3 tools/collab_gate.py check --role core --base 83b3607a96603e7e6ba18adbd62e1d57f24ed9f8` 因 A 分支不符退出码 1；无 A 专属门禁，不绕过。实现方需在本提交基线上单独通过 core 检查。
- 此处仅发布契约与服务编排；持久化实现与真实媒体/内网未验收。未安装依赖、未访问内网或运行 DJ。

- 角色：A；master；主工作区 /Users/hw-edit/Documents/ChatGPT/search-agent；未启动并行 worktree。
- 基线：cf17b1c5fd5e4218b9801bb82535c0b5555c6dfd；当前改动未提交。
- 状态：READY_FOR_REVIEW（本地切片），非全规格验收通过。
- 范围、契约变更与测试证据：见 implementation-slice-01.md、implementation-slice-02.md。CleaningExecutor 改为 operation 对象的 submit/poll/cancel，增加 CleaningRepository/CleaningCatalog；后续角色须同步新契约，不能沿用旧三参数 submit。
- 48 项单测通过；真实合成图片 DJ 服务联测通过；角色门禁失败（master 不符合 core 分支要求），未绕过检查。
- 内网、持久化重启、生产权限竞争和性能测试 NOT_RUN；未提交、推送、合并或改旧 DJ 源码。

## 后续集成（以上为历史记录）

- 用户已统一授权通过验证后的本地提交/合并，仍不推送、不部署、不接真实数据。
- core 在独立工作区通过角色门禁及 60 项测试，提交 aa179ea17527c3e4a8d24619fe65cb4f346de2ef，已快进集成。
- 当前角色 A / codex/integration / var/worktrees/integration，基于 aa179ea；任务卡 docs/dev/intake-task.md。
- CandidateIntake 接入服务端固定合成输入：预览 → 明确确认 → 不可变任务绑定清单，幂等重放、作用域/输入变更校验；实现 CleaningCatalog，清洗仍需单独明确确认。不实现自然语言检索、分页或空召回处理；当前固定清单要求 1–100 条，空清单按 INPUT_LIMIT 拒绝。
- 共同契约增加 CleaningCatalog.require_scope(task, manifest_ref)，清洗 prepare/start 和 core 预览读取前调用。既有 LocalCleaningCatalog 是项目级测试夹具；CandidateIntake 才增加 tenant/owner/project/task/revision/generation/confirmed-proposal 绑定。下游新适配器须实现该方法，不可静默跳过。
- A 接手 core 中唯一变动是上述契约调用，不改计数算法；无其他窗口在写代码。使用完整回归验证共享变更，不改责任表或检查器冒充 core 角色范围通过。
- 67 项单测通过，新增 7 项 intake 测试；git diff --check 通过。core 角色门禁在集成前 PASS；A 无专属角色检查器，不宣称共享变更通过 core 范围门禁。
- tools/cleaning_smoke.py 已串联固定候选确认 → 真实 DJ → 去重预览，4 张合成 PNG 含一对相同内容。主工作区集成 61f9b07 后真实运行 PASS：pass=3/fail=1/unknown=0，unique_pass_count=2、duplicate_count=1，accepted_count=null，任务 RUNNING。重复 start/poll 不重复发布。报告 var/cleaning-smoke.json，合成运行目录 /tmp/dhecdltxf（临时目录可能被清理）。独立集成工作区未共享 DJ 可变运行环境。
- 合并后在主工作区再次执行全部 67 项测试，通过；工作区干净。未推送或接真实数据。下一步优先提供可操作的演示入口；正式检索/结果发布仍须分别冻结契约，不能把固定候选来源改名为真实语义检索。
- 仍为单进程内存清单；跨系统授权/发布竞争不是生产事务保证。真实检索、业务全规则判定、持久化、正式结果发布与补数 NOT_RUN。

## 交互演示入口

- A / codex/integration / var/worktrees/integration，基线 bd395f2；任务卡 docs/dev/demo-task.md。
- 新入口 `python3 -m dsh_retrieval.demo --executor simulated|real`，依次人工确认需求、候选、清洗；无自动批准开关。执行器必须显式选择；缺少真实环境不自动降级。
- 新增 6 项测试，完整 73 项通过：三确认点停止不派发、完整模拟预览、超时取消、Ctrl-C 取消、提交传输异常的已预约 operation 栅栏、失败全 unknown。终端模拟模式实际运行通过，报告 /tmp/dkx1rm7us/demo-report.json。
- core 原交付再次运行角色检查 PASS（60 项），A 演示增量使用全量 73 项回归；A 无专属角色检查器，不伪造角色范围通过。
- 演示用短独立临时目录，报告标注 synthetic_fixed_input 与执行模式，保留证据但不提供进程重启恢复。未推送、部署或读取真实数据。
- 合并 87ee197 后，主工作区全量 73 项回归通过；真实终端交互 `--executor real` 经三个确认点运行成功，报告 /tmp/d2zhzqvqk/demo-report.json：status=preview_ready、清洗 3 pass/1 fail、唯一通过 2、重复 1、预览缺口 1、accepted_count=null、任务 RUNNING。这是开发者使用合成数据的验收，不代表用户已验收业务效果。

## 本地持久化与恢复（更新上述内存演示边界）

- A 分派基线 551eae4052fbc9e1dacf7c086ecc2972c0dc7d39，core 按 docs/dev/storage-task.md 在独立工作区完成 SQLite 仓库，角色门禁及 81 项测试 PASS，提交 78e457e 后集成。
- A / codex/integration / var/worktrees/integration 在 78e457e 上装配演示恢复；公共仓库协议不变。交互演示改用 workspace/ledger.sqlite3，清洗批准后以原子替换保存 session.json；HTTP 入口仍为内存，未宣称全部入口持久化。
- `--resume <原目录>` 校验原执行模式、任务/代次和源文件摘要，使用原任务/operation/批准摘要。已完成操作不重新提交；已批准而未完成的操作支持幂等继续；暂停/取消拒绝自动恢复。候选批准前状态、已清理临时媒体、旧演示目录不支持该恢复入口。
- A 新增 5 项测试，完整 86 项通过：完成会话由新进程恢复、模拟提交中断后的已预约 operation 重用、内容变化拒绝、模式不匹配拒绝、取消后拒绝。提交中断通过注入 SystemExit 模拟，非真实断电测试。
- SQLite 为本地开发单快照实现，16 MB 上限；schema format=1，版本不匹配或坏记录失败关闭，不自动清库。不是内网生产选型、SSE/outbox 或后台恢复调度；跨系统授权事务与断电故障注入尚未验证。无新增依赖、无 PostgreSQL 安装。
- 主工作区集成 3ae6249 后，86 项回归 PASS；真实 DJ 新会话 /tmp/dm6avsi8a 运行成功，再由独立 CLI 进程执行 `--executor real --resume /tmp/dm6avsi8a`，恢复 PASS。operation_id 始终 clean_e7dfd2bd0ddd43e5a197bed94d2e1aab，task_version=3、unique_pass_count=2 不变，原 DJ run_r001 未重新提交。证据 /tmp/dm6avsi8a/demo-report.json（合成临时数据）。这验证已完成作业跨进程恢复；真实运行中的强杀/断电恢复尚未实测。

## 2026-10-22 检索计划公共契约（未集成）

- 角色 A / codex/integration / /Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration；基线 b2cdedd7b966ad7e4756c127525f81dafd9400fc；任务卡 docs/dev/retrieval-contract-task.md。
- 状态 READY_FOR_REVIEW（契约级）；当前代码未提交，未发布新基线，主分支不变。上一轮 retrieval 模拟适配仍在其独立 worktree，本轮未复制或修改其代码，不把两边测试数量相加当集成验收。
- 新增 RetrievalScope / RetrievalPlan 与无副作用校验器；新增 RetrievalPlanRepository 端口，定义计划确认的 CAS、同一提案多键幂等、任务版本递增、page 预约与事务提交。contracts/retrieval-plan.md 明确请求、错误、兼容和未实现范围。
- 修改文件：src/dsh_retrieval/retrieval_contracts.py、src/dsh_retrieval/ports.py、tests/test_retrieval_contracts.py、contracts/retrieval-plan.md、docs/dev/retrieval-contract-task.md、本交接文件。
- 校验覆盖：创建人/租户、project/task/revision/generation/需求摘要/task_version；未确认及旧 epoch；暂停/取消/终态/归档/FINALIZING；请求篡改；编码器/维度；空页、深度上限、游标循环/前驱/快照变化、live 不声称耗尽；错误绑定/超页上限/非有限分数/损坏命中。
- 本轮不是确认服务或生产账本：校验函数不会批准/派发/写候选/更新计数。现有 CandidateIntake 不变，空检索页与清洗链路尚未装配；没有新的 HTTP 路由。
- 本地验证：2026-10-22 10:02（Asia/Shanghai），Python 3.13.3，`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q` 首轮 101 项通过、退出码 0；追加边界测试后结果见后续记录。
- 最终回归：2026-10-22 10:03（Asia/Shanghai），同一命令退出码 0，103 项通过（基线 86 + 本轮新增 17）；新增 FINALIZING 禁止提交及损坏命中显式错误用例。与 retrieval 分支的 104 项属于不同工作区，不是测试减少，也未完成双分支集成验证。
- 角色门禁：`python3 tools/collab_gate.py check --role retrieval --base b2cdedd7b966ad7e4756c127525f81dafd9400fc` 退出码 1，原因 codex/integration 不符合 retrieval 分支。A 没有对应角色检查器，不改责任表/检查器，不使用 baseline 模式替代；未宣称门禁通过。git diff --check 通过；未跟踪新增文件另检查空白。
- NOT_RUN：原子批准和幂等存储、真实权限撤销竞争、跨进程持久化、实际 ClickHouse/Milvus、真实 DJ、端到端检索→清洗；A20/A49 的实际授权/依赖故障不由纯校验器保证。
- 下一步：用户确认本地提交/集成权限后，先发布 A 契约基线；再派发 core 任务账本原子确认实现，retrieval 同步消费已批准记录，A 做端到端装配。现有检索模拟批准夹具不得替代账本，也不能将本契约记录标为 P01 完成。

## 本地集成授权与契约发布（2026-10-22）

- 用户已确认本地提交与审查后合并；不推送、不部署、不访问内网。上述“未获提交授权”为历史状态。
- A 重新审查公共契约、调用边界和测试，并在独立 integration 工作区重跑完整 103 项测试，退出码 0。只读审查 retrieval 适配切片，其原基线角色检查通过、104 项测试通过；下一步同步本契约基线后再验证组合。
- 本提交发布契约级开发基线。纯校验器、协议定义可用于后续开发；原子确认/任务事务/真实连接器尚未实现。A 无专属角色检查器的限制保持，不把完整回归冒充角色门禁。

## 检索模拟适配合入主分支（2026-10-22 10:14，Asia/Shanghai）

- 公共契约提交 4d666cd57907cc0e4abfa4e914e575469208b5b4 已快进合入 master，主工作区随后 103 项回归 PASS。
- retrieval 同步该契约基线，新增两项实际适配输出与公共校验器的组合用例。`python3 tools/collab_gate.py check --role retrieval --base 4d666cd57907cc0e4abfa4e914e575469208b5b4`：退出码 0，角色范围及 123 项测试 PASS。
- A 审查模拟适配代码及其验证边界后，提交 734efc3 已快进合入 master。合并后在主工作区执行 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`，退出码 0，123 项通过（86 基线 + 17 契约 + 18 适配 + 2 组合）。git 暂存 diff 空白检查通过，未改检查器或测试发现规则。
- 验收状态：ACCEPTED，仅公共契约/模拟适配开发切片。正式业务确认与检索计划持久化、候选发布/清洗装配、真实 ClickHouse/Milvus 全部未验收；P01 仍未完成。上文“待本地提交/基线未发布”记录已由此更新。
- 证据路径：tests/test_retrieval_contracts.py、tests/retrieval/test_simulated.py、docs/dev/handoffs/retrieval.md；本节记录实际执行结果，非真实 DJ 或内网联测。未推送、部署、安装依赖或接内网。
- 下一步：core 在本次集成基线上实现与任务共事务的检索计划账本，验收幂等确认、页预约/回传栅栏和重启恢复；A 再装配到业务流程。需要新任务卡及独立 core worktree，不能把模拟夹具当作已批准业务计划。

## 检索计划本地账本集成（2026-10-22 10:28，Asia/Shanghai）

- core 任务基线 7e9e33832352b22e2d68fcb82bdb86cca44973c1；任务卡/交接在 docs/dev/handoffs/core.md。实现提交 4db8db0，经 A 审查代码、角色范围及测试后快进合入 master。
- core 角色门禁通过；新增 21 项存储测试。合入后在主工作区执行 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`：退出码 0，144 项通过。无真实 DJ/内网执行。
- ACCEPTED 仅指本地存储切片：批准计划与任务版本同事务、同页预约/回传幂等、游标及版本栅栏、页与待发事件原子保存；新连接/新进程恢复原批准和预约。另以模拟网关验证两页预约→搜索→提交；没有自动候选发布或派发清洗。
- 旧任务/清洗 payload 不变；同库新增 retrieval_ledger 表，未触碰用户已有运行库。旧二进制不能直接读取新增表的账本。生产数据库选型未变，PostgreSQL 非必装。
- A 本轮只更新本记录及契约说明，无共享运行时代码变更；角色门禁证据采用本次 core 的实际通过结果，不宣称存在 A 专属检查器。
- 尚缺公共的待执行作业/已提交页读取接口、候选发布、HTTP/清洗装配和事件 relay；当前支持已知幂等键恢复，不宣称后台自动接管。下一步先冻结这些接口后分派实现。

## 授权作业读取契约（2026-10-22）

- A / codex/integration / var/worktrees/integration；基线 ecc58429096b94136627eae6b4fcab2e846926e5；任务卡 docs/dev/retrieval-read-task.md。响应 core 已登记的读取接口缺口，新增 RetrievalOperation、get_operation、list_pending，不改变已有写入接口或存储格式。
- 本提交发布契约基线，core 同步后实施。只读当前批准计划，区分 reserved 与已提交空页；重验身份/当前权限/版本；不派发、不加 lease，不作为媒体读取权限。
- 完整回归 146 项通过（原 144 + 2 返回模型用例），命令 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`，退出码 0。A 按要求运行 core 角色检查，因 codex/integration 非 core 分支退出码 1；没有 A 专属检查器，不改检查器绕过。core 实现需独立通过角色门禁。

## 授权读取本地集成（2026-10-22 10:50，Asia/Shanghai）

- 契约提交 8131b443b3222285d26a313f65574d4e52b3e913；core 在此基线上交付 0ad0609，角色检查通过。A 审查读取鉴权顺序、版本拒绝和无写入路径后快进合入主分支。
- 主分支合入契约后 146 项测试通过；合入实现后执行 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`，退出码 0，158 项通过（144 原基线 + 2 模型 + 12 读取）。独立测试发现检查确认 ID 唯一，不使用首次含重复发现的 179 次作为数量证据。
- ACCEPTED（本地授权读取切片）：无旧幂等键也可按任务列举待执行预约；可读取当前计划的已提交页，空页明确为 committed；读不改任务/事件、不自动执行；跨任务、旧 scope/plan、暂停/取消/撤权和故障失败关闭。
- 证据：tests/core/test_retrieval_reads.py、tests/test_retrieval_contracts.py、docs/dev/handoffs/core.md。数据库 schema 不变，未修改用户运行库、未访问内网或真实 DJ。
- 下一步候选发布/媒体物化仍缺契约与装配。作业读取不是历史结果浏览/媒体下载授权，也不是自动恢复调度或租约。当前没有承诺多个恢复进程不会重复发起外部请求。

## 授权媒体物化预览契约（2026-10-22）

- A / codex/integration / var/worktrees/integration；基线 4740d998e54961752dd062b796c9e5f8f0f538c9；范围与验收见 docs/dev/materialization-task.md。
- 新增内部 MaterializedPage/MaterializedCandidate 与 CandidateMaterializer 端口。公开 view 隐藏路径/媒体引用；摘要不依赖临时路径；不发布正式候选、不派发清洗，不计算有效素材数。
- 完整回归命令 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`：160 项通过、退出码 0（原 158 + 2 模型测试）。
- `python3 tools/collab_gate.py check --role retrieval --base 4740d998e54961752dd062b796c9e5f8f0f538c9` 退出码 1：A 分支不是 retrieval 分支；没有 A 专用检查器，不冒充门禁通过。实现方需在本契约提交基线上通过角色门禁。
- 真实媒体、内网、跨系统原子性和持久候选发布均未验证。

## 授权媒体物化预览集成（2026-10-22 11:17，Asia/Shanghai）

- 契约基线 10228c0ed84c45c49133e4f83868bd20af52a4f8；retrieval 实现 e559b505520f57ab30476ebc1c8886be9ee82e05，角色范围及 178 项测试通过。A 审查映射、身份聚合、读取上限、摘要和临时目录生命周期后快进合入 master。
- 主分支执行 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`：退出码 0，178 项通过（原 158 + 2 模型 + 18 物化）。
- ACCEPTED 仅本地临时候选预览开发切片：合成字节、模拟媒体网关与 SQLite；不是图片规则通过、真实召回、持久候选、清洗装配或内网验收。P01 仍未完成。
- 未新增依赖、未运行 DJ/DSH、未访问内网、未推送。下一步持久候选快照与独立确认契约，解决检索原 scope 和清洗递增 task_version 的生命周期衔接；强杀清理/跨系统 ACL 原子性未验证。

## A 总控门禁补齐（2026-10-22 15:54，Asia/Shanghai）

- 状态：实现与本地测试完成，待人工审查/可信基线发布；未提交、合并或推送。本记录不覆盖另一个 integration 工作区尚未提交的真实模型接入记录。
- 用户授权“补齐”严格门禁；角色 A，分支 codex/integration-gate，独立 worktree `/Users/hw-edit/.codex/worktrees/integration-gate/search-agent`，基线 22c62143dd20d49f0426f1fccd2232fd13de6699。范围见 docs/dev/integration-gate-task.md。
- 修改 tools/collab_gate.py、tests/test_collab_gate.py、docs/dev/ownership.json、docs/dev/parallel-plan.md、docs/dev/window-prompts.md；新增任务卡与 docs/dev/tasks/intent-model.json；更新本交接。未改模型代码、业务代码、依赖或旧测试发现规则。
- 新增 integration 角色：精确文件任务卡和责任表从可信基线读取；固定 codex/integration 分支、独立 linked worktree、起始提交祖先、验收说明、交接文件；文件范围取双重约束并排除原三个角色负责的路径。候选任务卡自改、未授权文件、路径穿越、目录通配、符号链接/子模块（含暂存链接被工作区普通文件掩盖）拒绝。
- 原 core/retrieval/cleaning 配置逐项与原基线相等，新增回归测试验证；它们不使用 --task 替代角色约束。
- 执行 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`：269 项通过、退出码 0（249 基线 + 20 新增门禁测试）。其中 30 项门禁测试含原有 10 项；测试中的预期 LOCAL GATE FAIL 是拒绝断言，不是全套测试失败。隔离 Git 仓库验证允许/拒绝路径；未访问内网、未调用真实模型/DJ。
- 执行 `python3 tools/collab_gate.py check --role core --base 22c62143dd20d49f0426f1fccd2232fd13de6699`：退出码 1，分支不匹配。执行新命令 `check --role integration --base 22c62143dd20d49f0426f1fccd2232fd13de6699 --task docs/dev/tasks/intent-model.json`：退出码 1，旧基线无 integration 配置。保持失败，不用 baseline 模式替代角色检查。git diff --check 通过。
- 后续：用户已一次性授权本地提交、检查和合并；门禁提交 c068af4 已快进发布到主分支，发布后的主分支 269 项回归通过。该首次发布由隔离测试和 diff 审查把关，不是新门禁自认证；不是 v1.9 全业务验收。

## 真实需求解析模型接入（2026-10-22 15:27，Asia/Shanghai）

- A / codex/integration / var/worktrees/integration；基线 22c62143dd20d49f0426f1fccd2232fd13de6699；范围与验收见 docs/dev/intent-model-task.md。按用户要求复用已配置凭据，持久澄清暂缓。
- 新增 src/dsh_retrieval/intent_model.py、tools/intent_model.py、tests/test_intent_model.py；更新 contracts/intent-review.md。默认模型 qwen3.7-plus，仅从私有配置取对应 key，不保存/打印凭据或端点，不改 DSH 配置。复用现有 YAML 解释器，无安装。
- 真实合成输入：`找10张客厅图片，不要文字和水印，横图优先，沙发要完整。`。首次 45 秒设置下连接失败（当时未区分超时，不能断言根因）；未带凭据的网关探测得到 HTTP 401，证明 TLS 可达。随后人工诊断调用使用 60 秒设置成功，无自动重试或更换模型。
- 实际返回 6 条解释：数量 10、客厅意图、text.present=false、watermark.present=false、orientation=landscape/soft、沙发完整 unresolved。澄清问题区分超出画幅与部件可见。后端原文定位/严格校验通过；execution_allowed=false、creates_task=false。
- 模型未包含“找”“图片，”及部分连接词/标点，审阅层追加 7 条未解析记录；未静默放行。默认能力目录为空，不能据此宣布 OCR/完整性/多规则清洗已支持。单样本不构成准确率或稳定性评测；无真实业务数据外发、无内网/DJ/正式任务执行。
- 本地回归：`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`，264 项通过、退出码 0（249 基线 + 15 适配测试）。测试包括鉴权错误脱敏、不重试、超时/断连、恶意字段/伪造片段、大小上限、原文定位及私有文件权限。
- 角色门禁：`python3 tools/collab_gate.py check --role core --base 22c62143dd20d49f0426f1fccd2232fd13de6699` 退出码 1：codex/integration 非 core 分支；现有工具没有 A 角色。未修改门禁，未宣称通过。本轮保留工作区修改待总控门禁处理，不自动合入主分支。
- 下一步：解决 A 交付门禁；接入持久澄清/修订，降低非语义片段澄清噪声并建立多样需求评测，然后对接确认和规则编译。当前为显式 CLI/适配器入口，不是网页端完整接入。

## 解释草稿与多规则预编译（2026-10-22 16:23，Asia/Shanghai）

- A / codex/integration / var/worktrees/integration；可信基线 92c1d0387477a990daa8bef76a53a91f04e09da0，任务卡 docs/dev/tasks/intent-draft-plan.json，范围见 docs/dev/intent-draft-plan-task.md。模型适配提交 07eedc4 已通过 integration 门禁，284 项回归通过，并快进合入主分支；此前门禁失败是历史记录。
- 新增独立的解释草稿 SQLite 存储与多规则编译预览、纯事实逐规则判定；修改文件 src/dsh_retrieval/intent_draft.py、src/dsh_retrieval/intent_compiler.py、tests/test_intent_draft.py、tests/test_intent_compiler.py、contracts/intent-review.md、本任务卡及交接。
- 草稿持久化原文、候选解释、版本及创建人/项目；请求键幂等，旧版本冲突，读取时复查当前项目权限与能力。数据库权限限所有者，重开新实例仍可读取。独立草稿不是正式业务任务、批次或 run。
- 编译预览需用户明确动作、版本和当前审阅摘要；未支持或待澄清硬条件拒绝。测试夹具的两条硬条件复用同一检测步骤，横图软偏好保留但不作为硬过滤；缺事实为 unknown、失败为 fail，均不能视为 pass。默认能力目录没有真实检测器，未开放自动执行。
- 首轮回归：`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`，295 项通过、退出码 0（284 基线 + 11 新增）。随后补充篡改预览拒绝用例；最终结果见下方门禁记录。覆盖重启恢复、幂等冲突、旧版本、越权、能力目录变化、绑定不匹配、共用步骤、软偏好和三态结果。
- NOT_RUN：真实 DJ 多规则执行、真实模型理解质量评测、内网 ClickHouse/Milvus、生产账本、正式确认/清洗任务装配、业务去重计数。下一步由 cleaning 角色在新契约上实现受信算子与事实协议，再做合成素材端到端验收；不能将本预览直接当作正式 CleaningRecipe。
- 角色门禁：`python3 tools/collab_gate.py check --role integration --base 92c1d0387477a990daa8bef76a53a91f04e09da0 --task docs/dev/tasks/intent-draft-plan.json`，范围 PASS、296 项测试 PASS、整体退出码 0（284 基线 + 12 新增）。测试输出中的 LOCAL GATE FAIL 是负向夹具预期，不是本命令失败。证据日期 2026-10-22 16:25（Asia/Shanghai）。

## 通用多规则清洗执行契约（2026-10-22）

- A / codex/integration / var/worktrees/integration；基线 5e1c1c0，任务卡 docs/dev/tasks/generic-cleaning-contract.json。用户选择先完成通用框架，暂无具体文字/水印检测模型或阈值决策。
- 新增 src/dsh_retrieval/multi_rule_contracts.py、tests/test_multi_rule_contracts.py、contracts/multi-rule-cleaning.md、docs/dev/generic-cleaning-task.md；仅冻结任务/版本/代次/清单/配方绑定、受信步骤/规则和事实结果结构。
- 3 项新增契约测试验证摘要绑定、重复/未映射/非法规则、无证据不得声称检测成功。未修改现有宽度 DJ 桥接，未新增用户可调用的执行入口。
- 2026-10-22（Asia/Shanghai）执行 `python3 tools/collab_gate.py check --role integration --base 5e1c1c0 --task docs/dev/tasks/generic-cleaning-contract.json`：范围 PASS，299 项完整测试通过（296 基线 + 3 契约），退出码 0；测试中的 LOCAL GATE FAIL 是负向门禁夹具预期。
- 下游 cleaning 角色将实现合成检测器通用执行器并在独立 worktree 跑角色门禁；具体检测器发布、真实 DJ 多规则、正式任务装配、公司内网仍 NOT_RUN。

## 审阅预览到不可变配方的桥接（2026-10-22）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；分派基线 `c2f61e6b1635b2949c0b5448bdfe8208f220f6d4`，任务卡 `docs/dev/tasks/multi-rule-recipe-bridge.json`；状态 READY_FOR_REVIEW。
- 新增 `src/dsh_retrieval/recipe_bridge.py`、`tests/test_recipe_bridge.py`，更新 `contracts/multi-rule-cleaning.md` 与本交接。共享输入输出仍用 `CleaningRecipe`，未改检测器或旧固定宽度 DJ 链路。
- 只从受信 `IntentCompiler.preview` 重新读取当前创建人、草稿版本、审阅摘要和能力绑定；严格校验预览摘要、字段、步骤、规则及逻辑后端引用，保留硬规则、排除软偏好。不是业务执行批准：不创建 `ApprovedCleaningJob`、不保存正式任务、不读素材或运行检测器。真实任务批准、结果持久化、发布时原子 fencing 仍待 core/总控后续切片。
- 验收：2026-10-22（Asia/Shanghai），`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_recipe_bridge.py -q` 退出码 0，5 项通过；`python3 tools/collab_gate.py check --role integration --base c2f61e6b1635b2949c0b5448bdfe8208f220f6d4 --task docs/dev/tasks/multi-rule-recipe-bridge.json` 退出码 0，313 项完整测试通过（308+5）。门禁输出中 LOCAL GATE FAIL 属负例夹具；本次结果为 LOCAL GATE PASS。
- NOT_RUN：正式多规则批准、真实 DJ 多规则、生产算子评测、ClickHouse/Milvus/W3、内网数据。合成测试只证明结构与拒绝边界，不证明模型理解或检测准确率。

## 已确认任务规则与配方核对（2026-10-22）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；基线 `6550e7954f008b7d125b09c330e10b3f8c7a46ef`，任务卡 `docs/dev/tasks/confirmed-recipe-rules.json`；状态 READY_FOR_REVIEW。
- 修改 `src/dsh_retrieval/recipe_bridge.py`、`tests/test_recipe_bridge.py`、`contracts/multi-rule-cleaning.md`、本交接。新增纯校验 `require_confirmed_rules`：对受信仓库已授权读取的正式任务，核对确认方案摘要、媒体类型、全部硬规则 ID/谓词/操作符/值及类型/顺序；软规则不作为硬过滤。旧/无确认、归档、摘要损坏、少/多/改规则均拒绝。
- 调用示例：`require_confirmed_rules(current_task, recipe)` 返回 `None` 表示规则结构匹配，不表示授权执行。兼容旧固定宽度链路，未改 Task/QuerySpec/SQLite 结构。影响后续 core 批准账本：它仍须绑定解释审阅来源、任务版本/generation、候选清单与配方摘要，并原子检查批准和发布栅栏。
- 2026-10-22（Asia/Shanghai），`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_recipe_bridge.py -q`：9 项通过（本轮新增 4 项）；`python3 tools/collab_gate.py check --role integration --base 6550e7954f008b7d125b09c330e10b3f8c7a46ef --task docs/dev/tasks/confirmed-recipe-rules.json`：SCOPE PASS、317 项完整回归通过（313+4），退出码 0。门禁输出内 LOCAL GATE FAIL 仅来自已有负例夹具，最终 LOCAL GATE PASS。
- NOT_RUN：正式批准持久化、作业装配/回传、真实 DJ 多规则、内网。校验器信任上游任务读取，不自行鉴权、创建作业或改变任务状态；不把规则等价误报成用户已批准本次执行。

## 多规则清洗批准契约（2026-10-22）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；基线 `38f1ce37496689d66f6fe68c4a1b5893bd4b6c77`，任务卡 `docs/dev/tasks/multi-rule-approval-contract.json`；状态 READY_FOR_REVIEW。
- 修改 `src/dsh_retrieval/multi_rule_contracts.py`、`tests/test_multi_rule_contracts.py`、`contracts/multi-rule-cleaning.md`、本交接。新增 `CleaningApprovalProposal`/`CleaningApproval`，冻结确认需求摘要、清单引用及内容摘要、配方摘要、任务版本与 operation；批准后版本严格 +1；`approval.job` 生成既有执行契约。无共享旧模型改动，不改变 SQLite 结构。
- 契约测试验证换清单/需求/配方摘要不同，重复素材、路径型引用、错误批准版本拒绝。数据类可在内存构造，不是授权或审批凭据；只允许 core 在受信事务中持久确认后返回作业。
- 2026-10-22（Asia/Shanghai），`python3 tools/collab_gate.py check --role integration --base 38f1ce37496689d66f6fe68c4a1b5893bd4b6c77 --task docs/dev/tasks/multi-rule-approval-contract.json`：退出码 0，SCOPE PASS、319 项回归通过（317+2），LOCAL GATE PASS。负例夹具中的 LOCAL GATE FAIL 不代表本次门禁失败。
- 待执行：core 独立 worktree 以 A 发布基线实现本地 SQLite 同事务确认和幂等记录，检查当前任务、确认规则、授权清单；撤权/旧版本不得批准。执行结果发布和真实 DJ 多规则另行验收。

## 检测器发布摘要批准绑定契约（2026-10-22）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；发布基线 `1c7c4c88bd07d4532087bfcf7c9bae19eafe3b1c`，任务卡 `docs/dev/tasks/detector-release-binding-contract.json`；状态 READY_FOR_REVIEW。
- 修改 `src/dsh_retrieval/multi_rule_contracts.py`、`tests/test_multi_rule_contracts.py`、`contracts/multi-rule-cleaning.md` 与本交接。`CleaningApprovalProposal` 和 `CleaningBinding` 新增可选 `detector_release_digest`；存在时进入 v2 提案/结果摘要并由 `approval.job` 原样传递。`require_released_cleaning_job` 统一拒绝缺少发布绑定的旧作业。
- 兼容边界：缺省值仅用于旧对象/账本分阶段迁移，保持原 v1 内容摘要，不能执行，也不能用当前发布静默补齐。下一步由 core 强制所有新批准保存发布摘要，再由 integration 在运行前与当前 `ReleasedDetectorRegistry.release_digest` 比对。
- 定向验证：`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_multi_rule_contracts -q`，10 项通过；`git diff --check` 通过。`python3 tools/collab_gate.py check --role integration --base 1c7c4c88bd07d4532087bfcf7c9bae19eafe3b1c --task docs/dev/tasks/detector-release-binding-contract.json` 退出码 0，SCOPE PASS、门禁内部 382 项通过、LOCAL GATE PASS；输出中的 LOCAL GATE FAIL 是既有负例夹具。未额外重复完整回归。
- NOT_RUN：真实 DJ、真实模型/制品、生产账本迁移、ClickHouse/Milvus/W3、内网数据；本轮只冻结共享身份与失败关闭边界，不宣称已完成持久化或运行时发布漂移拦截。

## 检测器发布摘要运行时栅栏（2026-10-22）

- A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；发布基线 `e3906f66b9b9f345d32df25f5189672775874d32`，任务卡 `docs/dev/tasks/detector-release-runtime-fence.json`；状态 READY_FOR_REVIEW。
- 修改 `multi_rule_pipeline.py`、`test_multi_rule_pipeline.py`、`contracts/multi-rule-cleaning.md` 与本交接。流水线在任何未完成执行或预览续发前，要求批准作业含发布摘要且等于服务端当前 `ReleasedDetectorRegistry.release_digest`；不匹配统一返回 `DETECTOR_NOT_RELEASED`，随后仍执行既有能力、检测器元数据和制品逐文件复核。
- 覆盖边界：旧批准、同逻辑 backend 下换能力目录/实现/模型清单、已保存结果但未发布预览均在素材读取和检测器调用前失败；已原子发布的历史预览仍通过业务账本重验当前权限/任务后只读重放，不重新执行或改写历史。
- 定向验证：`PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_multi_rule_pipeline -q`，11 项通过；`git diff --check` 通过。因修改正式流水线与公共契约，按仓库规则执行 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`，387 项通过；输出中的 LOCAL GATE FAIL 是既有负例夹具。
- 交付门禁：`python3 tools/collab_gate.py check --role integration --base e3906f66b9b9f345d32df25f5189672775874d32 --task docs/dev/tasks/detector-release-runtime-fence.json` 退出码 0，SCOPE PASS、门禁内部 387 项通过、LOCAL GATE PASS；输出中的 LOCAL GATE FAIL 是既有负例夹具。未额外重复完整回归。
- NOT_RUN：真实 DJ、真实检测器/模型制品、生产配置发布/回滚、ClickHouse/Milvus/W3、内网数据；合成测试只证明身份栅栏和恢复顺序，不证明模型质量。

## 开发进度与明细周期更新规范（2026-10-22）

- 22:15 CST 开工；A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`。首张任务卡 `docs/dev/tasks/development-progress-reporting.json` 试图包含根 `AGENTS.md`，22:16 CST 门禁以“任务卡不能授权其他角色或 A 范围外文件”退出码 1；未修改检查器/责任表绕过，已撤销该文件修改。
- 22:17 CST 恢复；修正版发布基线 `89ee3dc777fac183badd2bf9234d9e80a3298959`，任务卡 `docs/dev/tasks/development-progress-reporting-v2.json`，状态 IN_PROGRESS；范围仅保留 integration 允许的 `docs/dev/` 文件。
- 范围：`AGENTS.md`、`parallel-plan.md`、`window-prompts.md`、`handoff-template.md`、新增 `progress.md` 与本交接。选择“总控单一快照 + 各角色独立明细”，避免多工作区并发修改同一进度文件。
- 规则：活跃开发最长 30 分钟更新；开工、里程碑、阻塞/恢复、交付立即更新；短任务至少开工和交付。记录真实命令/结果和 NOT_RUN，无变化写原因，不把计划或模拟写成完成。
- 22:16 CST 里程碑：`python3 -m json.tool docs/dev/tasks/development-progress-reporting.json`、引用路径存在性检查及 `git diff --check` 均通过；integration 角色门禁待运行。纯文档规范变更，按 AGENTS 文档豁免不单独运行代码测试或完整回归。
- 22:17 CST 交付：`python3 tools/collab_gate.py check --role integration --base 89ee3dc777fac183badd2bf9234d9e80a3298959 --task docs/dev/tasks/development-progress-reporting-v2.json` 退出码 0，SCOPE PASS、内部 387 项通过、LOCAL GATE PASS；输出中的 LOCAL GATE FAIL 是既有负例夹具。状态 READY_FOR_REVIEW；没有额外重复完整回归。

## 受信检测器发布到清洗批准的唯一准备入口（2026-10-22）

- 22:32 CST 开工；A / `codex/integration` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration`；发布基线 `1606e5812910a3b19d9b8f17e4b95556f86f5031`，任务卡 `docs/dev/tasks/released-cleaning-approval-entry.json`；状态 IN_PROGRESS。
- 缺口：core 底层端口已要求 `detector_release_digest`，但尚无统一装配入口阻止受信层误传任意格式正确的摘要。本轮在 `multi_rule_pipeline.py` 增加入口，只从预检后的 registry 取摘要，再调用 core。
- 当前修改：入口先执行完整 registry 预检与配方复核；无效 registry、制品漂移、能力不匹配不得触达账本。测试、完整回归和门禁尚未运行，状态 NOT_RUN。
- 22:33 CST 里程碑：`PYTHONPATH=src:tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_multi_rule_pipeline -q` 退出码 0，15 项通过；`git diff --check` 通过。覆盖 registry 摘要写入、调用方摘要参数拒绝、不受信对象、制品漂移和能力缺失均不触达账本；完整回归与门禁待运行。
- 完整回归：`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q` 退出码 0，391 项通过；输出中的 LOCAL GATE FAIL 是既有负例夹具。integration 门禁待运行。
- 22:33 CST 交付：`python3 tools/collab_gate.py check --role integration --base 1606e5812910a3b19d9b8f17e4b95556f86f5031 --task docs/dev/tasks/released-cleaning-approval-entry.json` 退出码 0，SCOPE PASS、内部 391 项通过、LOCAL GATE PASS；输出中的 LOCAL GATE FAIL 是既有负例夹具。状态 READY_FOR_REVIEW。
- NOT_RUN：真实 DJ、真实检测器/模型、生产配置发布与回滚、ClickHouse/Milvus/W3、内网数据；入口只证明受信身份来源和失败关闭，不证明检测效果或完整产品装配。
# Chat 提单与反馈领域契约（2026-10-22）

- 角色：A integration；分支 `codex/integration`；基线 `9308a73`；任务卡 `docs/dev/tasks/chat-feedback-contract.json`；22:40 开工，22:43 完成端口与契约草案。
- 冻结 `save/get/confirm_feedback_draft` 与 `list_revisions` 端口：反馈草稿不改变正式版本或运行，确认绑定 draft/task 版本、完整摘要和幂等键，拒绝不影响当前正式版本。
- 首个体验版明确只把反馈文本追加进完整确认方案，不宣称已经把自然语言编译为可执行规则；真实 W3、检索、DJ、模型语义编译和内网均为 NOT_RUN/NOT_CONNECTED。
- 验证：`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q` 共 391 项通过；`python3 tools/collab_gate.py check --role integration --base 9308a7353df7663417942855ff468cc51cebcc4b --task docs/dev/tasks/chat-feedback-contract.json` 为 SCOPE PASS、内部 391 项通过、LOCAL GATE PASS。输出中的 `LOCAL GATE FAIL` 来自既有负向门禁夹具。
# 本地 Chat 提单与反馈体验（2026-10-22）

- 角色：A integration；分支 `codex/integration`；基线 `e3ad25881bd7b464dfb7aaa019139c6cdf023019`；任务卡 `docs/dev/tasks/chat-feedback-local-ui.json`；23:02 开工。
- 范围：依赖零新增的本地 HTML/CSS/JS、反馈/确认/版本 HTTP 路由、显式 SQLite 启动参数、端口补充及测试/说明。服务固定监听 loopback，页面使用 demo 身份。
- 页面明确区分“反馈需求版本已确认”和“自然语言规则已编译/执行”；后者仍未实现。真实检索、DJ、W3、内网均不在本切片。
- 23:10 里程碑：本地页面完成提单确认、反馈草稿、确认/拒绝和刷新恢复；HTTP 主流程、身份/幂等错误及页面安全头定向测试共 13 项通过。
- 23:13 交付验证：完整回归 405 项通过；`python3 tools/collab_gate.py check --role integration --base e3ad25881bd7b464dfb7aaa019139c6cdf023019 --task docs/dev/tasks/chat-feedback-local-ui.json` 为 SCOPE PASS、内部 405 项通过、LOCAL GATE PASS。输出中的 `LOCAL GATE FAIL` 为既有负向门禁夹具。
- 未验证：浏览器人工冒烟将在合并并启动后执行；真实检索、DJ、W3、模型语义编译和内网均 NOT_RUN。本地 SQLite 不是生产存储。
- 23:20 浏览器冒烟：确认卡创建 task revision 1；反馈确认发布 revision 2 / generation 2；刷新后从 SQLite 恢复同一任务。发现恢复页缺少新建入口并补充“新建另一单”；补充后 UI/API 定向 5 项通过。最终 integration 门禁 SCOPE PASS、内部 405 项通过、LOCAL GATE PASS。
# Chat 历史需求侧栏（2026-10-22）

- 角色：A integration；分支 `codex/integration`；基线 `43b07f7`；任务卡 `docs/dev/tasks/chat-history-local-ui.json`；23:35 开工。
- 范围：项目历史任务 GET、Chat 侧栏、正式 revision 展示与任务切换；明确不包含确认前逐句 conversation_message 草稿历史。
- 23:42 里程碑：项目任务列表路由、历史侧栏、正式 revision 展示、任务切换/恢复和空/失败状态已完成；HTTP/UI/core 定向 10 项及 JavaScript 语法检查通过。
- 23:45 交付：`python3 tools/collab_gate.py check --role integration --base 43b07f7ba3254b3f2cb4a7e1a2df2d9781d787cd --task docs/dev/tasks/chat-history-local-ui.json` 为 SCOPE PASS、完整 410 项通过、LOCAL GATE PASS；输出中的 `LOCAL GATE FAIL` 是既有负向门禁夹具。
- 23:48 浏览器冒烟：SQLite 中 2 条正式历史任务均出现在侧栏；点击“各种各样的兔子”成功展示 task、revision 1、方案摘要及继续反馈入口。体验服务已用最新 `master` 重启并保持在 `127.0.0.1:8080`。
- 未验证：真实 W3/内网、真实检索/DJ 和完整 conversation_message 草稿历史 NOT_RUN。
