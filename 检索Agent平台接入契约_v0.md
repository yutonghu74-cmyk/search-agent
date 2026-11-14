# 检索 Agent 平台接入契约（W3 身份与项目权限复用）

> 版本：v0.11（开发对接稿，待平台确认，2026-11-13）
> 甲方：数据平台（Camera_DMP_BACKEND，下称"平台"）
> 乙方：检索 Agent 服务（下称"Agent"）
> 范围：Agent 以**服务身份**嵌入平台，复用 W3 个人身份与平台项目权限体系。本文档区分平台现状说明、Agent 侧开发约束和待验证契约。可先开发适配层与模拟测试；待验证项不视为双方已确认，不据此启用真实业务访问。
>
> 事实依据：平台现状及源码位置由原稿提供；本仓库本次修订未独立核验平台源码、接口或测试。文件行号仅供平台核对，不能作为真实联测通过证据。

---

## 1. 现有应用的认证与项目接入示例

### 1.1 可信身份验证链路（平台现状，Agent 复用）

平台存在两条已落地的鉴权链路，Agent 按以下边界对接：

**链路 A：SSO Cookie 会话（浏览器场景，Agent 不适用，仅说明）**
- 浏览器携带 W3 SSO cookie → `SSOAuthBackend.authenticate()`（`src/middleware/auth.py:97`）→ `SSOLogin.get_w3_info()`（`src/middleware/hw_login.py:129`）反查 W3 用户 → `User` 表按 `account` 匹配（新旧工号兼容，`auth.py:144`）→ 首次登录自动建户（`plat_role=visitor`）。

**链路 B：JWT Bearer Token（服务间调用，Agent 采用此链路）**
- `POST /api/v1/auth/getDynamicToken`（`src/app/api/auth.py:42`）：传入 `app_name` + `account` + `credential`（=平台 `SECRET_KEY`）→ 签发 **24h JWT**（HS256，`auth.py:60`），payload 含 `appid / uid / username / account / roles`。
- 后续请求携带 `Authorization: Bearer <JWT>`，由 `_check_jwt_code()`（`src/middleware/auth.py:67`）统一解析为 `LoginUser`（uid/account/name/roles）。
- WS 场景走 `GET /auth/ws-token` 换 5 分钟短时 JWT，经 query 参数 `token` 传递（`auth.py:74`）——Agent 不默认开放终端；如有独立授权的 WS 日志需求，需另行验证适用性后复用。

**链路 C：W3 换发短期 PAT（对标 GitHub CLI，Agent 服务账户推荐升级路径）**
- 设计文档：`W3_TOKEN_EXCHANGE_PLAN.md`；实现：`src/app/api/third_party_cli.py`。
- 流程：`GET /third-party/local/cli/login`（浏览器 W3 登录，产出 5 分钟一次性配对码）→ `POST /third-party/local/cli/pair`（配对码换 `refresh_key`，明文仅返回一次）→ `POST /third-party/local/cli/exchange`（refresh_key 换 1h 短期 PAT，TTL=`LOCAL_CLI_PAT_TTL=3600`）。
- 三重校验：refresh 活跃 + 申请单 approved + 绑定 Key 活跃（`third_party_cli.py` `_validate_exchange_context`）。
- 明细见 `W3_TOKEN_EXCHANGE_USAGE.md`（完整用户手册，可直接作为 Agent 运维手册附录）。

### 1.2 当前项目传递方式（平台现状说明与 Agent 开发约束）

平台**项目（App）上下文通过 HTTP Header `x-app-id` 传递**，而非路径参数：

- 前端：pinia `userStore.app_id` → axios 拦截器统一注入 `x-app-id`（`Camera_DMP_FRONTEND/src/api/index.ts:57`）；`setAppId()`（`src/stores/modules/user.ts:69`）切换项目。
- 后端：各端点以 `app_id: T.Optional[int] = Header(alias="x-app-id")` 接收，典型处理：
  - 列表类：`GET /third-party/batch/tasks` → `_verify_app_membership()` 校验成员身份 → `filter(request__app_id=app_id)` 项目隔离（`src/app/api/third_party_batch.py:906-930`）；
  - 成员校验：`_verify_app_membership()`（`src/app/api/third_party_common.py:62`）——非 admin 且非 `app_member` 行 → 403；
  - 应用级权限：`BasicMethod.user_app_permission_valid()`（`src/utils/db.py:340`）——admin 直通，否则查 `AppMember`（`app_admin=True` 时要求 `app_role=ADMIN`）。

**Agent 侧接入约定（Agent 开发约束，平台待确认）**：
1. 个人业务访问优先采用链路 C 的个人 PAT，但必须先验证 PAT 对本节所列业务接口的适用范围。服务账户优先采用平台签发的独立、可撤销凭据；链路 B 仅记录平台现状，不作为向 Agent 分发平台签名密钥的实施授权。
2. 每次调用平台业务接口必须携带 `x-app-id` 头（值为 `app` 表主键 ID，整数）。
3. Agent 侧维护“会话 → (user_id, app_id)”不可变绑定；项目切换进入或新建目标项目会话，不修改原会话/任务归属。每次访问仍由服务端核验项目权限，浏览器传入的头不构成授权。

---

## 2. 用户、组织、项目的字段定义及关系

> 来源：`src/database/models/users.py`、`src/database/models/app.py`、`src/database/models/base.py`、`src/common/enums.py`。所有表继承 `BaseModel`：`id`(PK) / `created_at` / `updated_at` / `deleted_at`。

### 2.1 User（平台用户，表 `user`）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | 平台用户 ID（JWT 中的 `uid`） |
| account | varchar(20) unique | 工号（W3 uid，大小写不敏感，兼容新旧工号） |
| username | varchar(64) | 姓名（W3 `displayNameCn/En` 回填） |
| dept / dept_en | varchar(255) | 部门（W3 回填） |
| email | varchar(255) | 邮箱 |
| plat_role | enum UserRole | 平台角色：`super_admin` / `admin` / `player` / `visitor`（`enums.py:81`） |
| status | enum UserStatus | `normal` / `deleted`（`enums.py:88`） |
| last_access_time | datetime | 最近访问（每次鉴权刷新） |

### 2.2 App（项目/应用，表 `app`）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | 项目 ID（即 `x-app-id`） |
| name | varchar(128) unique | 项目名（仅小写字母/数字/`_`/`-`，≥3 字符） |
| alias | varchar(255) | 别名（展示名） |
| status | enum AppStatus | `normal` / `deleted`（软删，`enums.py:71`） |
| description | text | 描述 |
| creator_id | FK→User | 创建人 |
| sim_plat_app_id | int | 仿真平台应用 ID（联动用） |

### 2.3 AppMember（项目成员，表 `app_member`）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| user_id | FK→User | 成员 |
| app_id | FK→App | 项目 |
| app_role | enum UserRole | 项目角色：`admin` / `player` / `visitor`（默认 `player`） |
| creator_id | FK→User | 添加人 |

### 2.4 关系与组织口径（Agent 开发约束，平台待确认）

```
User 1 ──── N AppMember N ──── 1 App          （项目成员关系）
User 1 ──── N AppResponsible N ──── 1 App     （项目负责人，dataset_admin 语义，app.py:52）
App  1 ──── N CloudAppManagement               （项目绑定的云道应用/OBS 凭据，app.py:39）
User plat_role ∈ {super_admin, admin, player, visitor}   （平台级）
AppMember.app_role ∈ {admin, player, visitor}            （项目级，admin=项目管理员）
```

- **组织（部门）不是独立实体**：仅作为 `User.dept` 字符串字段存在，权限判定不基于部门，全部基于 `AppMember` 关系与角色。**按当前平台口径无需新增组织接口。** Agent 内部若需要 tenant_id，须使用服务端配置的稳定平台实例/组织命名空间；具体映射待确认，不以部门或浏览器参数推导。
- 角色判定快照（`BasicMethod.is_admin()`，`db.py:370`）：`roles` 含 `admin` 或 `super_admin` 即平台 admin。
- 用户删除为软删（`status=deleted`，`users.py:99`），成员移除见 §5。

---

## 3. 接口文档与脱敏请求响应

> 通用响应包（`src/app/schema/common.py:90`）：`{"data": <any>, "msg": "ok", "code": 200}`；分页包 `PageResult{items,total,page,page_size,total_pages,has_next,has_prev}`。
> 全部接口前缀 `/api/v1`。`x-app-id` 头为整数项目 ID。

### 3.1 用户可访问项目列表

**`GET /api/v1/app/list`**（`src/app/api/apps.py:238`）
- 鉴权：JWT（admin 可见全部；普通用户仅返回其 `AppMember` 所在项目，`apps.py:242-250`）
- 请求：`GET /app/list?page=1&page_size=10&search=xxx`（`PageParams`）
- 响应（脱敏）：

```json
{
  "code": 200, "msg": "ok",
  "data": {
    "items": [{
      "id": 101, "name": "project_alpha", "alias": "阿尔法项目",
      "status": "normal", "description": "示例项目",
      "creator": "a1***01", "created_at": "2026-10-01T08:00:00"
    }],
    "total": 2, "page": 1, "page_size": 10
  }
}
```

辅助端点（Agent 展示用户信息可用）：
- `GET /user/info`（`src/app/api/users.py:30`）→ 当前登录人 `UserInfo`（含 `app_info` 各项目角色、`all_apps`）。
- `GET /workbench/get_app_list`（`workbench.py:1408`）→ 工作台项目列表（含访问记录排序）。

### 3.2 项目操作鉴权（成员管理）

**`GET /api/v1/app/users/{app_id}`** 项目成员列表（`apps.py:146`，要求项目成员或 admin）
**`POST /api/v1/app/users/{app_id}`** 新增/改角色（`apps.py:85`，要求项目 admin，body `{"accounts":["a1***01"],"roles":["player"]}`）
**`DELETE /api/v1/app/users/{app_id}/{uid}`** 移除成员（`apps.py:126`，要求项目 admin；移除后同步删仿真平台侧用户，`apps.py:135`）
**`GET /api/v1/app/info/{app_id}`** 项目详情（`apps.py:76`，`user_app_permission_valid` 校验）

平台鉴权现状（待核验；Agent 资源仍须执行本地归属与发起人检查）：
| 操作 | 校验函数 | 通过条件 |
|---|---|---|
| 项目内读 | `user_app_permission_valid(app_id, user)` | 平台 admin 或 `AppMember` 存在 |
| 项目内管理 | `user_app_permission_valid(app_id, user, app_admin=True)` | 平台 admin 或 `app_role=ADMIN` |
| 项目数据隔离 | `_verify_app_membership(request, app_id)` | 同上（`third_party_common.py:62`） |

### 3.3 素材库及素材鉴权

> 平台无独立"素材库"概念，**素材 = 数据集体系**（BigDataset 大数据集 → Dataset 子集 → DatasetVersion 版本 → OBS 文件）。文件级元数据存 ClickHouse，经 `/search` 检索。

**`GET /api/v1/image_retriever/all_dataset`** 用户有权限的所有数据集（`src/app/api/image_retriever.py:21`）
- 权限并集：① `ResourcePermission(user, resource_type=dataset, resource_id=big_dataset_id)` 授权行；② 用户为 `AppMember`（admin/player）的 `app` 名下 `BigDataset`。
- 响应（脱敏）：

```json
{
  "code": 200, "msg": "ok",
  "data": [{
    "id": 88, "version_name": "v1.0（脱敏）", "dataset_id": 12,
    "dataset": "subset_***", "big_dataset_id": 5, "big_dataset": "alpha_***"
  }]
}
```

**`POST /api/v1/search`** 文件元数据检索（`src/app/api/search.py`，ClickHouse 后端）
- 请求（脱敏）：`{"target": "<app_name>", "search": [{"name": "version_id", "value": ["uuid***"], "query": "list_contains_any_one"}], "limit": 20, "offset": 0}`

**数据集 CRUD**（`src/app/api/data.py`）：`POST /data/big_dataset`、`GET /data/big_dataset/{id}`、`GET /data/big_datasets`（列表）、`GET /data/dataset/v/list/{dataset_id}`（版本列表）、`GET /data/bucket/files`（桶文件浏览）、`GET /data/object/preview`（临时访问 URL）。
- 创建数据集自动挂当前 `x-app-id` 项目；成员变更时系统自动补 `ResourcePermission(VIEWER)` 行（`apps.py:677-706`）。

**素材鉴权口径（Agent 开发约束，平台待确认）**：Agent 访问任何素材前调用 `GET /image_retriever/all_dataset`（或等价服务端校验）确认用户对该数据集有权限；**禁止 Agent 直接凭 OBS 路径读取文件**，文件下载必须走 `GET /data/object/preview` 换临时 URL。
> **待定①**：是否为 Agent 增设"素材级只读"专用接口（避免逐次全量拉权限集）。负责人：平台后端 owner（数据集模块）；只有在项目范围映射与当前授权均可验证时，才可使用 `all_dataset` 作为过渡目录；性能优化不得降低项目边界。

### 3.3.1 当前项目范围与素材读取（新增开发约束）

- 有效检索范围是“用户当前有权访问的数据集”与“当前 app_id 所属或被明确授权的数据集”的交集；`all_dataset` 的用户跨项目权限并集不能直接作为当前项目目录。
- 平台须提供按项目返回授权数据集的接口，或等价的可信映射，至少包含 app_id、big_dataset_id、dataset_id、version_id 及当前授权结论。共享数据集须显式关联授权项目，不按名称或 OBS 路径推断。
- `/search` 的 `target`、version_id 及向量检索过滤均由后端从上述授权范围构造；召回前限制范围，返回后复核素材归属与当前权限。无法安全限定的分支不启用。
- 预览/下载入口须验证用户、项目、数据集版本、具体对象及用途；临时 URL 本身不能作为长期权限证明。平台需补充 URL 有效期、访问约束、素材稳定ID/版本/摘要及命中映射样例。Agent 不接受模型或用户指定任意路径。
- 成员移除后 `ResourcePermission` 是否清理、独立授权是否保留，均待平台确认；无论历史授权行是否保留，用户失去当前项目资格后不得继续访问该项目 Agent 资源。

### 3.3.2 个人 PAT 的适用性证据（联调前必补）

平台提供个人 PAT 调用以下接口的脱敏请求与响应：`/user/info`、`/app/list`、`/app/info/{app_id}`、`/image_retriever/all_dataset`、`/search`、`/data/object/preview`。分别验证正常、非成员、跨项目、过期/吊销、账号停用及权限服务故障。

证据需说明认证中间件是否接受 PAT、实际解析的 user_id、所需 scope、HTTP 状态与业务 code；列出不支持的接口及替代方式。未验证前不得将个人 PAT 仅支持模型调用等能力推定为支持全部业务 API。

### 3.4 批跑/任务接口（Agent 触发处理任务时复用）

`POST /third-party/batch/task`（建单，绑定申请单→项目）、`GET /third-party/batch/tasks`（列表，`x-app-id` 隔离）、`GET /third-party/batch/task/{tid}`（详情）、`POST /third-party/batch/cancel/{tid}`（撤销，仅申请人）。任务归属：`APIRequest.project_id`（`src/database/models/third_party_api.py:231`）——**任务通过申请单间接挂项目**，鉴权与可见性见 §4。

---

## 4. 项目内任务与结果的可见性规则；成员移除后的处理要求

### 4.1 可见性规则（原稿提供的平台现状，Agent 侧未验证）

来源：`third_party_batch.py:904-972`、`tests/e2e/test_batch_visibility_expr_flow.py:18`（P0-5 用例），原稿记录如下，待平台核对：

| # | 规则 | 实现位置 |
|---|---|---|
| V1 | 带 `x-app-id` 的列表/详情：非 admin 必须是 `app_member`，否则 403"无权访问该项目数据" | `_verify_app_membership`（`third_party_common.py:62`） |
| V2 | 列表按项目过滤：`filter(request__app_id=app_id)`，项目间互不可见 | `third_party_batch.py:929` |
| V3 | 详情/日志兜底：任务有申请单项目时，即使用户不传 `x-app-id` 也按 `task.request.app_id` 校验成员身份 | `third_party_batch.py:963-969` |
| V4 | 平台 admin 可绕过成员检查；项目列表过滤与详情的项目匹配行为仍须分别实测，不能据此允许 Agent 跨项目展示 | `third_party_common.py:64` |
| V5 | 用量/费用归因：落 `APIUsage(user_id, app_id)`——人扣申请单 `last_balance`，任务呈现归申请单绑定项目 | `W3_TOKEN_EXCHANGE_USAGE.md` §6、`third_party_batch.py:1258` |
| V6 | 留案（prompt/响应全文、MD5 结论）仅本人/admin 可见 | `GET /third-party/local/usage`（`third_party_local.py`） |

**Agent 侧要求（Agent 开发约束，平台待确认）**：Agent 展示任务/结果时，必须以用户身份带 `x-app-id` 透传给平台接口，由平台完成过滤；项目列表等展示缓存 TTL ≤60s；会话不是权限缓存有效期。检索、媒体读取、导出、结果发布及后台继续执行前均须核验当前权限；权限服务不可用时拒绝受限操作。Agent 自有资源另按下述规则鉴权。

### 4.1.1 Agent 自有资源的可见性（开发基线）

| 资源/操作 | 默认规则 |
|---|---|
| Chat、消息、附件与需求草稿 | 当前项目内仅发起人可见 |
| 正式任务、候选、清洗证据及未发布结果 | 当前项目内仅发起人可见 |
| 修改、确认、推进与取消 | 仅需求发起人；项目管理员不自动获得代确认权 |
| 项目内共享 | 须发起人显式共享，并核验接收人的当前项目和素材权限；第一版未实现时关闭 |
| 跨项目引用或共享 | 第一版关闭；不得由用户跨项目成员身份自动推导 |

平台已有批跑接口的项目成员可见规则仅描述平台资源，不能直接覆盖上述 Agent 资源。Agent 对任务/子记录/预览/下载/SSE 做归属检查，平台 admin 的鉴权绕过不自动授予 Agent 私有内容读取权；平台如需运维访问，另行明确授权和审计规则。

所有资源绑定服务端确定的项目与身份。DSH 会话、记忆、工作目录、缓存、分页游标、幂等键和事件按相应用途绑定项目、用户及资源版本；切项目不得复用另一项目的消息或上下文。平台批跑单仅在确有处理需要且业务批准后创建，不替代 Agent 草稿或正式任务账本。

### 4.2 成员移除后的处理要求（Agent 开发约束，平台待确认）

现状语义（`DELETE /app/users/{app_id}/{uid}`，`apps.py:126-142`）：
1. 删除 `AppMember` 行 + 同步删仿真平台侧用户；
2. 移除后该用户对项目内列表/详情立即 403（V1/V3 实时查表，无缓存窗口；成员查询有 60s 页面缓存 `@cache(expire=60, namespace="app")`，仅影响管理界面展示，不影响鉴权）。

Agent 必须满足：
- **R1**：收到 403 后立即失效该用户的项目上下文，停止展示该项目任务/结果，不得以本地缓存续命；
- **R2**：用户级展示缓存（项目列表、目录）TTL ≤60s；不可用作受限操作的授权依据。活动页面/SSE 最迟60s复核项目资格，失败停止推送受限内容；每次受限操作仍实时核验。
- **R3**：原稿描述 PAT 最长1h自然过期，不能据此承诺即时吊销。平台需明确每次调用是否检查 PAT/refresh 吊销及账号状态；未验证前不得声称即时生效。Agent 收到401立即失效对应凭据与受限上下文，等待重新认证。
- **R4**：成员移除**不删除**其历史任务与留案数据（平台侧任务归项目所有，仅失去可见性）。
> **待定②**：平台是否提供"成员变更事件推送"（webhook/消息队列），替代 Agent 轮询感知移除。负责人：平台后端 owner（三方API模块）；未上线前 展示按 R2 轮询复核，操作前实时核验。

---

## 5. 测试环境、测试用户、测试项目及测试素材

### 5.1 测试环境（原稿记录，待核验）

| 项 | 值 | 说明 |
|---|---|---|
| 测试库 | `DB_HOST=<TEST_DB_HOST>`（datamesh 库，PostgreSQL） | 共享测试库，**Agent 禁止对库执行任何 DDL**（见 AGENTS.md 红线 R1-R5） |
| 后端服务 | 本地起服 `.\run_server.ps1` → `python -m src.main`，端口 `5001`（`settings.port`） | `http://<host>:5001/api/v1/...` |
| E2E 验证 | `pytest tests/e2e/ -m e2e`（64 用例；`-m smoke` 7 条冒烟） | schema 门禁：漂移自动 SKIP |
| 前端 | `Camera_DMP_FRONTEND`（vite dev） | |

### 5.2 测试用户（仅保留角色占位符，实际账户由内部安全渠道提供）

来源：`tests/e2e/conftest.py:224-258`（`_TEST_ACCOUNTS`）：

| 角色 | 工号 | 姓名 | plat_role | 用途 |
|---|---|---|---|---|
| 测试人 | `test_user_a` | 测试用户A | player | 普通用户视角（Agent 主测试账户） |
| 密钥审批人 | `test_admin` | 平台管理员 | admin | 管理员视角（项目/成员/审批操作） |
| 批跑审批人 | `test_batch_user` | 批跑测试用户 | player | 批跑审批链路 |

Agent 测试通过平台正式测试登录/换发入口取得受限凭据，不复制平台签名密钥，不自行签发可访问平台的 JWT。单元测试可使用不连真实平台的模拟凭据。

新增普通测试用户 `test_user_b`（非平台 admin）：仅加入 beta。初始矩阵为 alpha 仅用户A、beta 仅用户B；另在独立测试步骤把用户A加入 beta 验证共同成员，再移除验证撤权。具体项目矩阵由平台确认，admin 不能代替普通用户隔离测试。

### 5.3 测试项目

- E2E 用例动态创建随机 ID 项目（`test_batch_visibility_expr_flow.py:28`：`project_p = random.randint(100000, 999999)`），页面集中查看项目 **"E2E自动化测试"**（`tests/e2e/README.md`）。
- **拟申请给 Agent 联调的固定项目（2 个，尚未创建确认）**：
  - `agent_dev_alpha`（ID 由内部受控渠道提供）——成员：test_user_a(player)、test_admin(admin)
  - `agent_dev_beta`（ID 由内部受控渠道提供）——成员：test_user_b(player)、test_admin(admin)；用于验证普通用户间的项目隔离
> **待定③**：上述两项目的正式创建与 ID 分配（由 admin 经 `POST /app/?creator_id=` 创建，`apps.py:177`）。负责人：平台侧管理员（test_admin 平台管理员）；创建后通过内部受控渠道交付实际 ID，并注入 Agent 私有配置。

### 5.4 测试素材（少量）

- 挂 `agent_dev_alpha` 项目下：1 个大数据集（BigDataset，含 2 个子集 Dataset、每子集 1 个版本 DatasetVersion），桶内样本文件约 10-20 个（图片/视频各若干）。
- 素材准备路径：admin 创建大数据集（`POST /data/big_dataset`，自动挂 `x-app-id`）→ 上传样本 → 成员 test_user_a 经 `ResourcePermission(VIEWER)` 自动授权（成员添加时系统补授权行，`apps.py:677`）。
> **待定④**：测试素材的实际文件来源与上传排期。负责人：平台侧数据管理员（与待定③同一人执行）；样本建议直接复用 E2E 真机批跑用例的图像子集（`docs/进度/真机批跑用例评估_图像编辑子集_2026-11-06.md`）。

---

## 6. 后台服务身份、凭据注入方式和部署存储要求

### 6.1 后台服务身份（Agent 开发约束，平台待确认）

- Agent 服务账户以**平台用户**形式存在（`User` 表一行，工号由平台管理员分配，`plat_role=player`；不使用平台 admin 作为跨项目访问兜底）。
- 服务账户加入需接入的项目（`AppMember`，`app_role=player`），按项目逐一授权，不使用平台 admin 兜底。
- 服务间调用鉴权：优先由平台以独立、最小权限、可撤销凭据签发短期访问令牌；服务账户 PAT 是否覆盖业务接口需实测。禁止将平台全局 `SECRET_KEY` 分发给 Agent。具体服务认证流程待平台确认。
> **待定⑤**：Agent 服务账户工号申请（走 W3 开户流程）及加入测试项目的操作窗口。负责人：平台管理员（test_admin 平台管理员）。

### 6.1.1 后台执行的双重授权

- 服务身份负责访问执行基础设施，个人授权决定该用户在该项目可执行的业务范围。服务账户加入项目不能替代发起人的资格核验。
- 每个作业持久绑定发起人、app_id、task_id、revision、generation 及批准范围；凭据不写入作业、模型上下文或日志。
- 每次检索、媒体读取、导出、派发后续作业及结果发布前，核验账号状态、当前项目成员资格和素材用途权限。采用用户凭据或平台认可的委托鉴权接口；具体协议待确认。无法代表发起人可靠核验时，暂停受限操作，不回退为服务账户权限。
- 浏览器关闭或普通登出不自动取消已批准任务；凭据不可用时等待重新授权。账号停用、项目撤权或权限服务故障阻止新的受限操作；在途回传还须复验当前授权、任务版本和 generation，拒绝旧结果覆盖。

### 6.2 凭据注入方式（Agent 开发约束，平台待确认）

| 凭据 | 注入方式 | 禁止事项 |
|---|---|---|
| 平台 `SECRET_KEY` | 仅由平台持有和使用 | 不向 Agent 分发，不用于 Agent 自签 JWT |
| refresh_key（链路 C） | 环境变量 / 密管系统；明文仅签发响应返回一次（平台只存 SHA256） | 禁止落盘明文缓存（内存持有即可，丢失重新 `cli/login` 签发） |
| 短期 PAT | **仅内存缓存**，TTL 3600s，剩余 <10% 时预换发（`W3_TOKEN_EXCHANGE_PLAN.md` §5.2） | 禁止写配置文件/数据库 |
| 换发限频 | exchange ≤ 30 次/小时/refresh（429 保护，`settings.py:206`） | Agent 必须缓存 PAT，禁止每请求换发 |

参照实现（平台已提供，Agent 直接仿写）：`W3_TOKEN_EXCHANGE_USAGE.md` §4.2 原生 openai SDK + 手动换发样例（`exchange()` + `get_pat()` 约 15 行）。

### 6.3 部署与存储要求（Agent 开发约束，平台待确认）

- **部署形态**：Agent 作为独立 HTTP 服务部署（内网），与平台后端同网络域（需访问 `w3cloud/efs` W3 接口、平台 API、ClickHouse 检索层）。参考训练 agent 同构决策："agent 独立服务化，与平台之间用 HTTP API + 配置"（`docs/方案/训练模块融合落地方案.md` 关键决策 2）。
- **状态存储**：Agent 通过业务存储接口持久化 Chat/消息、需求草稿、正式任务、批次/版本/generation、确认、幂等、作业/outbox、结果及交付关系，保存稳定 user_id/app_id 与必要审计依据；不另建人工维护的账号、项目和权限主库。草稿不得创建正式 task/batch/revision/run。PostgreSQL 仅为候选实现，真实试点前须验证重启恢复与持久化。PAT 仅内存缓存，长期 refresh 凭据按密管规范保存。
- **网络要求**：出向可达 `<PLATFORM_API_HOST>`（或测试环境 `:5001`）与 W3 域（`<W3_LOGIN_HOST>`、`<W3_API_HOST>`，见 `hw_login.py:32-34,123`）。
- **日志红线**：凭据（JWT/PAT/refresh_key/SECRET_KEY）与用户 prompt 全文不得出现在 Agent 日志（对齐平台留案脱敏实践，`LOCAL_CALL_LOG_TRIM` 策略 `settings.py:210`）。
- **密钥泄露处置**：`dmp refresh revoke`（级联吊销 PAT）→ 重新签发；平台侧吊销入口 `POST /third-party/local/cli/refresh/{id}/revoke`（本人或 admin，`third_party_cli.py`）。

---

## 7. 待确认项与开发/联调门槛

| # | 待确认或验证内容 | 负责人角色 | 门槛 |
|---|---|---|---|
| ① | 素材级只读接口及可替代的实时鉴权方案 | 平台数据集后端 owner | 专用接口可后补；等价安全校验不可缺 |
| ② | 成员变更事件推送 | 平台三方 API owner | 可后补；先按展示复核和操作前实时校验 |
| ③ | 两个测试项目的实际 ID 与普通用户成员矩阵 | 平台管理员 | 隔离联调前完成 |
| ④ | 合成/获准测试素材及版本、映射、预览样例 | 平台数据管理员 | 素材联调前完成 |
| ⑤ | 最小权限服务账户及独立凭据签发流程 | 平台认证 owner | 服务身份接线前完成 |
| ⑥ | 个人 PAT 对各业务接口的支持和错误响应证据 | 平台认证及接口 owner | 个人身份接线前完成 |
| ⑦ | 当前项目授权数据集映射及检索前范围约束 | 平台数据集/检索 owner | 真实检索前完成 |
| ⑧ | 后台代表发起人鉴权、吊销/账号停用检查协议 | 平台认证/权限 owner | 后台真实执行前完成 |
| ⑨ | 成员移除后的 ResourcePermission 处理与共享授权语义 | 平台权限 owner | 撤权联调前完成 |
| ⑩ | Agent 可见性基线、平台实例到 tenant_id 映射、嵌入入口与可信会话建立方式 | 双方产品及后端 owner | 对应生产能力启用前确认 |
| ⑪ | 持久化部署、重启恢复与凭据重新获取方式 | Agent 与平台运维 owner | 真实试点前验收 |

负责人姓名、真实环境地址、账号、项目ID、凭据及获准测试数据清单通过内部受控渠道交付；本仓库仅保留角色及脱敏占位符。各 owner 具体人选待平台指派。

### 7.1 开发与启用边界

Agent 侧实现进展见第8节；实际平台协议与环境未验证的能力仍标记 NOT_CONNECTED/NOT_RUN，不以模拟授权、声明式配置模板或开发身份头兜底生产认证。

本稿为 Agent 侧修订建议，不宣称双方已完成冻结。原稿中的现状说明、源码引用和平台 E2E 结论仍需平台核对；修订后的业务口径须双方确认后形成冻结版本。不得以“待定项不阻塞开发”解释为允许跳过对应上线门槛。

### 7.2 定向验收清单

- 两名非 admin 用户在两个项目上的仅单方成员、共同成员及非成员访问；伪造/缺失 `x-app-id`、跨项目任务ID、子记录、游标和幂等键不能越权。
- 切换项目后消息、模型上下文、DSH 记忆、文件和缓存无串用；同项目非发起人不能读取默认私有资源或代确认。
- 用户跨项目数据集权限并集不会扩大当前项目召回范围；共享数据集仅在显式授权项目内可用。
- 成员移除、素材撤权、账号停用、凭据过期/吊销及权限服务异常时，页面、SSE、预览、导出和后台操作均按约定阻断。
- 服务账户仍有权限而发起人已撤权时，后台不能继续；旧 revision/generation 回传不能覆盖新结果。
- 重启恢复保留项目归属、草稿/任务/确认、幂等和未完成作业；无有效凭据时不自行恢复受限访问。

以上为待执行验收要求，本次仅修订文档，未运行平台测试、创建账号/项目或修改真实业务数据。

## 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.9 | 2026-11-13 | 原始平台现状与接入草稿；原稿冻结声明由本次修订替代 |
| v0.10 | 2026-11-13 | 区分现状与待验证契约；补充个人/服务双重授权、PAT验证、项目数据交集、私有可见性、撤权和持久化；脱敏环境与人员信息，补齐联调门槛 |


## 8. Agent 侧实现进展（v0.11）

本仓库已新增平台专用启动及业务装配、个人应用会话/CSRF、项目切换与浏览器存储隔离、加密 refresh 委托/内存 PAT/重启恢复、当前项目素材权限交集、检索前版本过滤/命中复核、绑定项目与版本的分页游标，以及受控临时媒体读取；复用现有任务批准、候选、清洗、补数和交付账本。

接线默认使用声明式私有 wire 配置，不预设未确认的响应字段；缺少完整映射不能启动为可用平台接入。具体配置、HTTP契约、部署限制与进入条件见 [平台接入实现与部署](docs/dev/platform-integration.md)。平台侧未知事项和第7节内网验收门槛仍有效，本节不宣称双方契约已冻结或真实联调通过。

本版文字输入检索沿用已有能力；平台参考图编码未验证时明确拒绝，不因上传接口存在而宣称跨模态查询已接通。

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.11 | 2026-11-13 | 登记平台侧接线开发、声明式字段映射、部署说明及真实联调边界；不改变未确认的平台协议 |
