# DSH Retrieval Agent

## 当前本地开发入口（2026-10-31）

新版统一入口为 `http://127.0.0.1:8094/`：首页为生产总览，`/chat` 为需求对话，
`/production?task=…&node=…` 为节点输出，`/catalog` 为索引配置。页面和业务 API
由同一个后端提供；这是使用模拟身份的本地开发环境，不是对外部署。

8094 沿用 `var/local-clip-live-20261030/` 下的任务、对话、目录和独立 DSH_HOME，
以及既有私有模型设置和本地图片配置。8085 是保留的旧体验服务及独立历史账本，
未迁移或合并到 8094；下文关于 8085 干净源码发布的约定继续适用于旧体验服务。

原 8098 只读预览可用以下命令转为新版入口跳转（启动前先确保 8094 可用，且 8098 未被占用）：

```bash
PYTHONPATH=src python3 tools/production_preview.py --unified
```

跳转保留节点路径和查询参数；新建需求、模型调用、检索和交付均直接访问 8094，
不经只读预览代理。不要同时启动多个服务写入同一任务账本。

基于《DSH 检索 Agent：设计方案与落地规格书 v1.5》启动的 P0 契约基线。

当前设计基线为 [v1.9：存储解耦与既有清洗系统复用](规格书/DSH检索Agent_设计方案与落地规格书_v1.9.md)；
v1.8 及更早版本保留供追溯。代码仍为 P0 切片，尚未实现新版完整能力。
后续复用既有 DJ `plan_flow` 作为清洗执行组件，由本项目承接检索、补数、
项目批次和结果账本。业务确认仅由需求发起人处理；重复点击或重试不重复执行。

当前实现刻意只覆盖可独立验证的第一条纵向切片：

- 新草稿使用 `query-spec@2` 的图片/手动模式子集；`query-spec@1` 仅保留历史校验；
- 规范化 JSON 与 SHA-256 摘要；
- 项目草稿创建/读取/修订、发起人明确确认、幂等创建任务与批次、不可变方案快照；
- 任务运行状态转换和 generation 栅栏；
- OpenAPI 3.1、JSON Schema、PostgreSQL DDL；
- A01、A22、A52、A64 基础测试及 A97/A104/A106 的部分本地测试，非完整验收。

DSH 文本 Agent 已有独立 SDK 适配，需在启动时显式配置 `--dsh-python` 和 `--dsh-home` 才启用。真实 Milvus、素材授权和身份系统尚未接入。DJ 已接入内部 CleaningService，并通过合成图片真实联测；尚未开放清洗 HTTP 入口，不会在确认任务时自动执行。
组件通过 `ports.py` 中的端口接入，不能用当前内存实现进入生产。确认后的任务停留在 PLANNING，当前没有调度 Worker。

用户登录已确定对接内部 W3，当前状态为 `NOT_CONNECTED`，本地身份头仍仅供开发。
接入所需资料与边界见 [W3 登录对接约定](docs/p0/w3-integration.md)。

公司内部使用 ClickHouse 和 Milvus，暂无可复用的 PostgreSQL。
任务服务已通过 `TaskRepository` 接口与存储解耦；HTTP 开发入口仍使用内存适配器，交互演示已使用本地 SQLite 快照适配器；
生产任务存储保持待选，PostgreSQL DDL 仅为候选草案。
参见 [内网对接约定](docs/p0/internal-integration.md) 和
[对接配置模板](config/internal-integration.example.json)。模板尚未接入运行时加载。

DSH 的独立开发环境已安装在 `.venv-dsh`。文本 Agent 入口使用固定 SDK/运行时 `0.2.1rc2`；依赖见 `requirements-dsh.lock`。运行任何 `dsh` 命令都必须显式提供隔离的 `DSH_HOME`，禁止隐式共享用户级目录。真实检索/清洗的生产接入状态仍按各模块单独验收。

配置真实模型时，API 默认使用 DSH 推理与受限提案工具。例如在配置所引用的密钥环境变量已提供后：

```bash
PYTHONPATH=src python3 -m dsh_retrieval.api --demo-project --port 8094 \
  --model-config /absolute/private/models.json \
  --dsh-python /absolute/path/to/.venv-dsh/bin/python \
  --dsh-home "$PWD/var/dsh/preview" \
  --ledger "$PWD/var/preview/tasks.sqlite3" \
  --dialogue-ledger "$PWD/var/preview/dialogue.sqlite3"
```

各 worktree 使用自己的端口、账本和 DSH_HOME。SDK 不必安装进业务服务的 Python；
`--dsh-python` 启动独立 worker。`--agent-runtime direct` 只用于显式旧版诊断，
DSH 错误不会自动回退。运行边界见 [模型运行契约](contracts/model-runtime.md)。

体验版只从已提交且干净的 `master` 启动，命令增加 `--require-clean-source`。
`GET /v1/build` 与页面右侧“体验环境说明”显示启动时的完整提交号、源码状态、模型运行方式和检索模式。
更新时先完成测试和提交，再重启 8085；任务、对话、目录 SQLite 及独立 `DSH_HOME`
沿用原路径。服务运行期间修改源码不会改变已加载的 Python 代码，需重启后核对版本号。
当前本地检索夹具显示为“本地测试索引”，不能当作真实 Milvus 接入。

## 本地运行

默认开发流程见根目录 [AGENTS.md](AGENTS.md)：直接在当前代码上实施并运行相关测试，
无需先创建任务卡、角色分支或独立 worktree。确有多人并行时参考[可选协作方案](docs/dev/parallel-plan.md)
和[并行提示](docs/dev/window-prompts.md)；既有任务卡的协作门禁仍可使用。
本地测试通过不能当作全量业务验收通过。
当前开工依赖与接口差距见 [基线就绪检查](docs/dev/baseline-readiness.md)。本地 Data-Juicer
目录暂不纳入 Git，新的 worktree 仍需独立准备经过校验的清洗依赖。

业务服务使用 Python 标准库，要求 Python 3.11+；DSH worker 使用独立锁定的 SDK 环境。

```bash
# 测试入口会自行配置 src、仓库根目录与 tests 导入路径
python3 tools/run_tests.py --all -v
# 定向示例：模块、类、方法或 tests 下的 Python 文件
python3 tools/run_tests.py tests.test_query_routing -v
PYTHONPATH=src python3 -m dsh_retrieval.api --demo-project --port 8080
```

完整用法和真实依赖测试分层见[测试工程策略](docs/dev/testing-strategy.md)。

DSH 离线验证：

```bash
DSH_HOME="$PWD/var/dsh/local-smoke" .venv-dsh/bin/dsh --version
DSH_HOME="$PWD/var/dsh/local-smoke" .venv-dsh/bin/dsh --profile sdk-minimal --help
```

开发服务器只监听 loopback。`--demo-project` 显式允许 tenant-a/user-a 访问 demo/default；不加此参数默认拒绝所有项目。
本地身份头可被伪造，不能暴露为生产服务。先保存草稿：

```bash
curl -i http://127.0.0.1:8080/v1/retrieval/projects/demo/drafts \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-1' \
  -H 'X-Tenant-Id: tenant-a' \
  -H 'X-User-Id: user-a' \
  --data-binary @contracts/examples/draft.json
```

展示返回的完整 `proposal` 后，向 `POST /v1/retrieval/tasks` 发送
`draft_id`、`expected_draft_version`（草稿返回的版本）、`proposal_digest`、`action: "confirm"`，
并携带新的 Idempotency-Key。重复确认返回同一 task_id/batch_id；旧版直接创建请求返回 422。
本轮只启用图片目标、文字需求、五节点 manual、本批 exact-content、无历史去重、input_only/manual；
未实现的策略明确拒绝，不自动降级。接口用于业务状态验证，不承诺实际素材产出。

端到端与契约验证（前者需要本机 loopback 端口权限）：

```bash
PYTHONPATH=src python3 tools/api_smoke.py
var/dj-runtime/bin/python tools/validate_api_contracts.py
```

DJ 环境恢复及图片探针见 [第一轮落地记录](docs/dev/implementation-slice-01.md)。

内部清洗提交、取消栅栏及逐素材回传见 [第二轮落地记录](docs/dev/implementation-slice-02.md)。

固定合成候选接入、明确确认、真实 DJ 与去重预览的串联入口：
`PYTHONPATH=src python3 tools/cleaning_smoke.py`。
需先准备前述独立 DJ 环境；候选来源是模拟固定输入，不是语义检索，不查询 ClickHouse/Milvus。
该演示生成 4 张图片（含一对内容重复），预期清洗通过 3 张、唯一通过 2 张；
accepted_count 仍为空，任务不自动完成。最新进度见 [总控交接](docs/dev/handoffs/integration.md)。

### 交互演示（终端版）

在仓库根目录执行：

```bash
# 无额外依赖，明确标注模拟执行；不代表真实 DJ 验证
PYTHONPATH=src python3 -m dsh_retrieval.demo --executor simulated

# 使用已经准备好的本工作区 DJ 环境；不自动安装或退回模拟
PYTHONPATH=src python3 -m dsh_retrieval.demo --executor real
```

依次查看需求、4 张固定合成候选和宽度清洗规则，每步输入 `确认` 或 `y` 才继续。
其他输入停止；执行中 Ctrl-C 请求取消并先撤销结果提交资格。没有自动批准参数。
预期宽度通过 3、失败 1、重复剔除 1、唯一通过 2，目标 3 的预览缺口为 1；不自动补数。
报告、合成图片与本地 SQLite 账本保留在终端显示的 `/tmp/d...` 目录，可能被系统清理。
本次升级后，已确认清洗的会话支持恢复（用实际目录替换下方占位符）：

```bash
PYTHONPATH=src python3 -m dsh_retrieval.demo --executor real --resume /tmp/d实际目录
```

必须使用原执行模式，模拟会话用 `--executor simulated`。恢复沿用原任务/operation，已完成作业只读回结果，未完成作业继续查询或重放同一提交；仍需明确确认。
已取消/暂停的任务不会自动重跑。清洗确认之前的退出、源图片丢失/变化、旧版内存演示目录不支持此恢复入口。
SQLite 目前是上限 16 MB 的单快照开发账本，验证本机事务与进程重启，不是正式生产存储、outbox 或内网数据库选型。
这不是最终业务交付，`accepted_count` 仍为空；真实模式只代表 DJ 确实运行，不代表真实库检索或全部业务规则通过。

### Chat 提单与反馈体验（网页本地版）

```bash
mkdir -p var/chat-demo
PYTHONPATH=src python3 -m dsh_retrieval.api --demo-project --ledger var/chat-demo/ledger.sqlite3 --port 8080
```

浏览器打开 `http://127.0.0.1:8080/`。页面支持自由文本生成确认卡、确认提单、保存反馈草稿、确认或拒绝反馈、发布新的需求 revision，并从“历史记录”侧栏分别查看草稿会话和正式任务版本；SQLite 账本支持刷新和进程重启后读取。草稿消息不进入业务执行时间线，旧账本不会伪造或补回功能上线前未记录的聊天。

输入 `动物，狗扩20个词，猫扩30个词` 可体验显式标签分组：狗、猫作为主标签分别形成独立交付包，扩展词只作为辅助召回词。当前使用 `local-template@1` 确定性模板并要求确认，不调用模型；“扩 N 个词”不会被误解为每组交付 N 张图片，分组交付配额保持未指定。

打开正式任务会显示绑定当前 revision/generation 的五节点只读进度。没有权威结果集时，有效数量和缺口显示“未知”，不生成估算完成百分比；此视图不代表后台作业已经启动。

正式任务同时读取当前结果视图：没有绑定当前版本的已发布结果集时明确显示“结果尚未发布”；存在权威结果时才展示有效、备选、重复、淘汰、待复核和缺口，并以固定结果摘要分页。当前页面不提供图片地址，素材预览仍需后续接入授权媒体网关。

该页面只监听 loopback，使用可伪造的本地身份和固定 demo 项目授权。反馈文本会进入新的完整需求快照，但尚未编译成真实清洗规则，也不会启动真实检索、DJ、补数或交付。不要将服务暴露到局域网或公网。

### 本地首次检索体验（显式启用）

Chat 对首次诉求提供 LLM 意图分析预览；反馈则单独判断能否形成图片清洗条件，不重复展示首次提单的分析卡。复制 `config/chat-models.example.json` 到仓库外的私有配置文件，填入受信 HTTPS 接口及真实模型 ID；在启动服务的环境中提供 `DSH_CHAT_LLM_API_KEY`，再追加 `--model-config /私有路径/chat-models.json`。密钥只从环境变量读取，不要放在 JSON、页面或仓库中。服务端仅有一个受信 LLM/VLM 配置时，页面会默认选中它们，并在发送区显示调用状态；用户仍可切回模板。首次诉求发送后，对话区显示助手理解的条件和需澄清内容，逐条引文与能力边界可展开查看。这是对结构化模型结果的归纳，不是具备长期记忆的自由对话。原文明确的目标数量仅在用户未手动修改数量框时填入待确认草稿；冲突会提示，不覆盖用户填写。首次诉求的模型分析失败会停在草稿前，不会悄悄退回模板。模型分析和扩词在用户确认前不会创建正式任务或启动检索；用户点击“确认需求与扩词，开始检索”后才批准这条本地流程。仅选 LLM 时扩词仍为模板，LLM 扩词另需同时配置并选择 VLM。当前本地模式仍使用可伪造身份，只限隔离测试，不传真实业务诉求。页面中的“使用 API 与密钥配置”目前只在页面内暂存填写内容，不会启用或改动服务端模型；生效配置必须按上述启动方式提供。

当前反馈入口与首次提单不同：发送反馈后生成独立草稿，再调用 LLM 判断它能否形成图片清洗条件。可用反馈只以自然语言展示候选解释；无关或不明确的反馈只提示重新描述，不显示候选 VLM 指令或确认按钮。模型建议并未写入反馈确认摘要；当前“仅保存反馈文本”只更新需求版本，不启动清洗。模型不可用时可先查看完整草稿再手动决定。

首次需求现在使用单一对话输入框（Enter 发送，Shift+Enter 换行）：模型先分析图片内容和目标数量，缺任一项会追问，不创建正式任务；补齐后按类别分别扩词，以 `ceil(目标张数/100)`（最少 1、建议最多 30）作为给模型的参考，不强制凑足。词表可直接编辑，也可在确认前填写反馈并点击“按反馈重新扩词”，模型结合原需求和当前词表给出新版提案。若用户明确说“要五个召回词”，显式词数优先，返回每类恰好五词；“不是狗的情绪”等排除条件会约束扩词。已确认任务收到明确的扩词/召回词反馈时，先在对话中直接展示新版词表，不再误入清洗建议；用户点击“确认新版词表”才发布同一任务 ID 的新版本，并更新目标张数。点击“确认需求与扩词，开始检索”才创建带持久任务 ID 的正式需求，并将本次检索节点设为自动模式：每个词只有一个可用授权索引时直接准备、批准并分批执行本地检索，每批刷新对话中带检索词来源标签的原始候选，直到各分支结束或遇到需处理的错误/上限。某词有多个授权索引时只请用户选择有歧义的词；任何词无索引或无法编码则整组停止，不跳过。正式任务 ID 不因检索完成而变化；后续反馈沿用同一任务 ID，通过 revision/generation 区分版本。五节点和任务 ID 固定显示在右栏。历史任务不会因打开页面而自动检索；尚未检索的历史需求会在对话中提供“开始逐词检索”，已检索的历史需求会恢复本地候选。“查看查询分支”等开发期诊断入口已收在反馈框底部的可选折叠区。原始候选不能算作清洗合格图片。当前 `LOCAL_EXACT` 夹具只支持预置查询向量，真实 LLM 扩出的任意词通常无法逐词编码，不会把固定样例冒充检索结果。完整逐词检索仍需接入匹配索引的 embedding 服务和真实向量库。

后续消息不是按关键词硬分流：LLM 会结合当前需求和词表，判断“少找点”“多找户外”“别按情绪搜”等隐含调整检索的意思，并与“结果里有水印的不要”这类清洗条件区分；无法判断时请用户补充，不默认为清洗。

此模式使用固定测试向量进行实际相似度排序，支持两索引选择、整组确认、主对话自动逐批检索（诊断入口仍可手动逐批）和授权后的原始候选列表；不是 Milvus、学习型 embedding、真实缩略图或清洗结果。先在当前终端设置私有且重启保持不变的 `DSH_LOCAL_CURSOR_SECRET`（至少 32 字节，勿写入仓库），然后在仓库根目录运行：

```bash
mkdir -p var/first-search-local/dsh
DSH_HOME="$PWD/var/first-search-local/dsh" PYTHONPATH=src python3 -m dsh_retrieval.api \
  --demo-project --port 8084 \
  --ledger var/first-search-local/tasks.sqlite3 \
  --catalog-source config/local-first-search-catalog.example.json \
  --catalog-ledger var/first-search-local/catalog.sqlite3 \
  --query-capabilities config/local-first-search-capabilities.example.json \
  --local-first-search-fixture config/local-first-search.example.json
```

打开 `http://127.0.0.1:8084/`，用“找动物图片”提单并确认，再点击“本地首次检索（需确认）”：勾选狗、猫两个索引，生成并确认整组检索卡，最后显式执行下一批。列表展示来源绑定、向量主键、素材映射状态；同 ID 跨库保持隔离，缺失映射不计有效数。固定示例允许在没有模型配置时使用受信本地目录，且不会替用户保存索引配置或批准检索。重启时保留同一任务账本、目录夹具和游标密钥，才能安全续页；夹具变化会拒绝旧计划。

对话式逐词检索的本地演示可将 `--local-first-search-fixture` 改为 `config/local-first-search-guided.example.json`。该夹具只用显式登记的狗/猫/动物词规则映射到测试向量，能处理原文附带数量和以主标签开头的辅助词；未登记的词明确报不可编码。它不是语义 embedding，结果仍只是本地原始候选。不要在运行中的旧版本服务上直接替换原夹具；需以新代码和新夹具启动独立服务或受控重启。

本地身份头可伪造，服务只监听 loopback；不要接真实素材、内网凭据或生产账本。页面不提供图片内容，原始命中不是合格素材，`accepted_count` 仍未知。真实 Milvus/模型/授权媒体接入须单独验收。

## 目录

- `src/dsh_retrieval/`：领域契约、状态机、应用服务和最小 HTTP 适配器。
- `contracts/`：供前后端和后续 Agent/配方编译器共用的冻结候选契约。
- `db/migrations/`：可选 PostgreSQL 业务账本 DDL 草案，未应用。
- `tests/`：无外部服务的契约与状态测试。
- `docs/p0/`：能力基线和待决参数；`UNRESOLVED` 不得作为门禁通过。
