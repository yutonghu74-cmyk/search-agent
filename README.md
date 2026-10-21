# DSH Retrieval Agent

基于《DSH 检索 Agent：设计方案与落地规格书 v1.5》启动的 P0 契约基线。

当前设计基线为 [v1.9：存储解耦与既有清洗系统复用](规格书/DSH检索Agent_设计方案与落地规格书_v1.9.md)；
v1.8 及更早版本保留供追溯。代码仍为 P0 切片，尚未实现新版完整能力。
后续复用既有 DJ `plan_flow` 作为清洗执行组件，由本项目承接检索、补数、
项目批次和结果账本。业务确认仅由需求发起人处理；重复点击或重试不重复执行。

当前实现刻意只覆盖可独立验证的第一条纵向切片：

- 严格的 `query-spec@1` 结构与业务校验；
- 规范化 JSON 与 SHA-256 摘要；
- 可信请求身份、任务创建、幂等重放与任务读取；
- 任务运行状态转换和 generation 栅栏；
- OpenAPI 3.1、JSON Schema、PostgreSQL DDL；
- 对应 A01、A22、A52、A64 的基础测试。

DSH、Milvus、Data-Juicer 和真实身份系统尚未接入。它们必须在各自版本与能力清单冻结后，通过 `ports.py` 中的端口接入，不能用当前内存实现进入生产。

用户登录已确定对接内部 W3，当前状态为 `NOT_CONNECTED`，本地身份头仍仅供开发。
接入所需资料与边界见 [W3 登录对接约定](docs/p0/w3-integration.md)。

公司内部使用 ClickHouse 和 Milvus，暂无可复用的 PostgreSQL。
任务服务已通过 `TaskRepository` 接口与存储解耦，外部开发使用内存适配器；
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
PYTHONPATH=src python3 -m dsh_retrieval.api
```

DSH 离线验证：

```bash
DSH_HOME="$PWD/var/dsh/local-smoke" .venv-dsh/bin/dsh --version
DSH_HOME="$PWD/var/dsh/local-smoke" .venv-dsh/bin/dsh --profile sdk-minimal --help
```

开发服务器监听 `127.0.0.1:8080`。本地请求使用 `X-Tenant-Id` 和 `X-User-Id` 模拟网关注入的可信身份：

```bash
curl -i http://127.0.0.1:8080/v1/retrieval/tasks \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-1' \
  -H 'X-Tenant-Id: tenant-a' \
  -H 'X-User-Id: user-a' \
  -d '{"request":"找 10 张室内家居图，不要水印","media_type":"image","target_count":10,"business_domain":"home_lifestyle","auto_refill":true,"execution_mode":"interactive"}'
```

## 目录

- `src/dsh_retrieval/`：领域契约、状态机、应用服务和最小 HTTP 适配器。
- `contracts/`：供前后端和后续 Agent/配方编译器共用的冻结候选契约。
- `db/migrations/`：可选 PostgreSQL 业务账本 DDL 草案，未应用。
- `tests/`：无外部服务的契约与状态测试。
- `docs/p0/`：能力基线和待决参数；`UNRESOLVED` 不得作为门禁通过。
