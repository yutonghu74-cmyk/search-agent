# DSH Retrieval Agent

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

DSH、Milvus 和真实身份系统尚未接入。DJ 已接入内部 CleaningService，并通过合成图片真实联测；尚未开放清洗 HTTP 入口，不会在确认任务时自动执行。
组件通过 `ports.py` 中的端口接入，不能用当前内存实现进入生产。确认后的任务停留在 PLANNING，当前没有调度 Worker。

用户登录已确定对接内部 W3，当前状态为 `NOT_CONNECTED`，本地身份头仍仅供开发。
接入所需资料与边界见 [W3 登录对接约定](docs/p0/w3-integration.md)。

公司内部使用 ClickHouse 和 Milvus，暂无可复用的 PostgreSQL。
任务服务已通过 `TaskRepository` 接口与存储解耦；HTTP 开发入口仍使用内存适配器，交互演示已使用本地 SQLite 快照适配器；
生产任务存储保持待选，PostgreSQL DDL 仅为候选草案。
参见 [内网对接约定](docs/p0/internal-integration.md) 和
[对接配置模板](config/internal-integration.example.json)。模板尚未接入运行时加载。

DSH 的独立开发环境已安装在 `.venv-dsh`。当前冻结候选为 SDK/运行时 `0.2.1rc2`；依赖见 `requirements-dsh.lock`。运行任何 `dsh` 命令都必须显式提供隔离的 `DSH_HOME`，禁止隐式共享用户级目录。

## 本地运行

多窗口协作见 [实施方案](docs/dev/parallel-plan.md)、[窗口任务说明](docs/dev/window-prompts.md)
和根目录 [AGENTS.md](AGENTS.md)。本地基础门禁：`python3 tools/collab_gate.py baseline`；
开发窗口必须另外通过带角色与基线的 `check`，不能把基础测试通过当作全量业务验收通过。
当前开工依赖与接口差距见 [基线就绪检查](docs/dev/baseline-readiness.md)。本地 Data-Juicer
目录暂不纳入 Git，新的 worktree 仍需独立准备经过校验的清洗依赖。

项目只使用 Python 标准库，要求 Python 3.11+。

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m dsh_retrieval.api --demo-project --port 8080
```

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

该页面只监听 loopback，使用可伪造的本地身份和固定 demo 项目授权。反馈文本会进入新的完整需求快照，但尚未编译成真实清洗规则，也不会启动真实检索、DJ、补数或交付。不要将服务暴露到局域网或公网。

## 目录

- `src/dsh_retrieval/`：领域契约、状态机、应用服务和最小 HTTP 适配器。
- `contracts/`：供前后端和后续 Agent/配方编译器共用的冻结候选契约。
- `db/migrations/`：可选 PostgreSQL 业务账本 DDL 草案，未应用。
- `tests/`：无外部服务的契约与状态测试。
- `docs/p0/`：能力基线和待决参数；`UNRESOLVED` 不得作为门禁通过。
