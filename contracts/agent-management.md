# Agent 接入管理首片

本片提供可选后端管理服务和 HTTP 入口：登记后端已安装实现、验证发布、项目绑定、授权发现。没有安装/上传代码、任意 endpoint、团队市场或管理门户；未配置时返回 `AGENT_MANAGEMENT_UNAVAILABLE`，不改变现有部署。

## HTTP

基础路径：`/v1/retrieval/projects/{project_id}/agent-management`。

| 方法 | 后缀 | 请求 |
| --- | --- | --- |
| GET | 无 | 返回项目登记目录、发布证据引用、安装快照是否匹配、当前绑定与版本 |
| POST | `/register` | `{"implementation_ref":"team-search@1"}` |
| POST | `/publish` | `{"implementation_ref":"team-search@1"}` |
| POST | `/bindings` | `{"expected_version":0,"routes":{"retrieval":"team-search@1"}}` |

POST 必须携带 `Idempotency-Key`。同主体/租户/项目下，同键同体返回原回执，同键异体返回 409。绑定更新要求当前 `expected_version`，并发编辑只有一个成功。`routes` 是完整替换，空对象显式关闭该项目的所有规划角色；未配置管理绑定的项目也返回空路由，不回退静态默认。平台默认实现亦须作为已安装发布登记并显式绑定。

登记项固定实现名称/版本、角色及输出契约、角色/运行/产物摘要、适配器 ID、连接配置引用。配置引用必须是后端逻辑标识，不得含地址、凭据或文件内容；请求不能覆盖这些元数据。已登记快照不可原地变更，升级应使用新实现引用。不同项目各自登记/发布/绑定，同一项目内可由具有管理权限的其他成员维护。

## 后端装配

`AgentManagement(path, installed=..., roles=..., adapters=..., authorize=..., authorize_release=..., qualify=...)`：

- `path` 为显式持久 SQLite 文件；短连接、事务和跨线程比较更新，重启保留目录、证据、绑定和幂等回执。不是仅内存目录；首次部署要备份并在真实环境验证恢复。
- `installed` 为后端安装的 `InstalledAgent` 对象目录。`roles` 来自实际 runner 的角色定义；不兼容角色/输出契约在构造时拒绝。
- `adapters` 为已安装 `AdapterRegistry`。发布和绑定使用实际 `probe()` 校验协议、产物及所需能力，额外要求 `model_call_admission`；不以 `describe()` 或模型文字作为准入。
- `authorize(actor, project, operation)` 必须在拒绝时抛出异常；`operation` 为 `read/register/publish/bind`。部署方应把管理写权限与普通项目读权限分开，并使用已核验身份。
- `authorize_release(actor, project, installed_agent)` 负责团队实现对具体项目的授权，防止项目成员登记其他团队的私有实现。解析当前项目路由时再次核验；撤权拒绝后续选择，不自动回退。
- `qualify(actor, project, installed_agent, adapter)` 为可信后端资格检查，返回已验证证据引用。须核对工具隔离、取消、期限、模型/资源上限及未知完成状态。不能仅相信 probe 自报，不能使用客户端提供的证据。失败抛异常或返回无效引用，发布事务回滚。

资格检查目前同步执行于管理事务中，应只做有界检查/核验既有证据，不在回调内开展长时模型运行或回调本管理服务。远程异步验证作业、证据有效期/撤销与完整审计历史留后续切片。测试采用明确的资格夹具，不代表真实独立 Agent 已准入。

接入既有运行和 HTTP：

```python
planning = build_agent_planning_service(
    task_service, runtime_config,
    authority=authority, context_provider=context_provider,
    model_environment=model_environment, model_binding=model_binding,
    adapters_provider=adapters_provider,
    agent_management=management,
)
handler = create_handler(task_service, agent_planning=planning,
                         agent_management=management)
```

已有作业/Chat 装配继续使用同一个 `planning`，不能创建平行任务服务。`agent_management` 与原 `project_routes` 参数互斥。构造 runner 时检查管理目录与实际运行目录的角色和运行发布摘要一致，不能用同名发布替换另一产物。目录中的所有发布须列入后端运行配置，旧版本继续保留以服务已入队运行。只登记实现不会自动修改运行配置，也不会自动获得业务工具或执行权。

项目绑定变化进入原阶段配置校验：未启动的旧审阅需要重新审阅；已入队尝试沿用持久账本原发布，重启不切换为新绑定。仍执行既有版本、generation、确认和工具预算边界。管理目录发布不代表正式业务任务可直接执行。

HTTP 通过 `create_handler(..., agent_management=management)` 显式开启。生产环境仍须使用已有 `identity_mode='platform'` 和真实权限适配；开发头部身份不是生产认证。共享集成凭证沿用既有访问白名单，没有新增管理权限，不能代替人或部署管理者发布/绑定。

## 验证与剩余范围

定向覆盖目录重启、发布失败回滚、角色契约不兼容、并发绑定、幂等冲突、跨租户/项目/实现授权、产物变更拒绝、HTTP 链路，以及真实持久规划夹具中的绑定变更/旧审阅失效/入队后重启版本固定。未运行全量回归或真实独立 Agent 准入。

尚需真实身份/团队授权装配、真实适配器资格证据、管理页面、自助发现可安装目录、异步验证、停用/撤销及审计。当前服务未接入或重启本机 8096，未修改真实业务数据。
