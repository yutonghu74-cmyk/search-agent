# 公司内网对接约定（草案）

2026-10-20：用户确认内部已有 ClickHouse、Milvus，暂无可复用的
PostgreSQL，允许保留任务存储接口。本决策已纳入
[v1.9 规格书](../../规格书/DSH检索Agent_设计方案与落地规格书_v1.9.md) 第 4.3—4.5 节。
当前开发无需安装 PostgreSQL；生产任务存储选型仍待确定。

外部开发使用内存测试实现；若需跨重启调试，可增加 SQLite 本地适配器
（当前未实现）。正式试点处理真实任务前须选定并验证持久化实现，
保留接口不表示生产存储已就绪。

## 当前代码与接入位置

身份提供方已确定为 W3，尚未完成接线；协议、用户字段和网关/直连方式
按 [W3 登录对接约定](w3-integration.md) 待内部接口资料确认。

业务接口统一定义在 `src/dsh_retrieval/ports.py`，数据结构定义在
`integration_models.py`。驱动类型、SQL、内部表字段、连接地址均留在
`adapters/` 的具体实现内。

| 接口 | 内网接入方向 | 当前状态 |
| --- | --- | --- |
| TaskRepository | 后续选定的持久存储或内部任务 API | 创建/读取契约及内存适配器已实现 |
| CatalogRepository | ClickHouse 或目录服务 | 接口草案 |
| MediaRepository | ClickHouse 媒体映射表或素材服务 | 接口草案 |
| VectorSearchGateway | Milvus SDK 或内部检索 API | 接口草案 |
| EmbeddingProvider | 内部查询编码服务 | 接口草案 |
| AuthorizationService | 内部权限服务 | 接口草案 |
| MediaAccessGateway | 内部素材读取服务 | 接口草案 |

`RetrievalGateway.start_batch` 是协调器派发作业的接口；
`VectorSearchGateway.search` 是 Worker 执行检索的接口，两者层次不同。
当前只有任务创建/读取服务使用了可替换接口，检索链路尚未接线。

HTTP 的接线入口为 `api.create_handler(TaskService(repository))`。
以后将具体实现传入此入口即可替换当前任务存储，业务服务不导入数据库驱动。
启动命令目前仍显式使用内存实现，进程退出后数据丢失。

## TaskRepository 的最低保证

- 创建任务与登记幂等记录是一个原子操作；相同身份、路由、key 和摘要返回原任务，
  不同摘要返回 `IDEMPOTENCY_CONFLICT`。
- 读取和重放按 tenant/user 隔离；无权限与不存在统一 `NOT_FOUND`。
- 返回独立对象；调用者修改返回值不能绕过持久层写入。
- 持久实现还须通过跨进程并发及进程重启后的重放测试；内存锁不证明这些能力。

当前接口只覆盖已实现的 create/get 切片。控制命令上线时需增加事务级方法：
一次提交完成 expected_task_version 检查、运行代次更新、命令幂等、事件/outbox 写入；
Worker 提交还需同一事务核验 revision/generation/plan_version。
不能拆成无条件 get 后 save。以上后续写接口尚未实现，也未通过持久化验收。

业务确认仅由需求发起人处理，不增加转派、多人共同审批或会签。
连续点击、多标签页提交、网络超时重试应返回原处理结果，不重复创建任务、
启动批次或推进流程；同 key 不同正文、已处理事项的新决定或陈旧版本返回冲突。
取消与系统完成仍需原子控制，这与多人审批无关。

原 `db/migrations/001_initial.sql` 保留为 PostgreSQL 候选草案；
不自动执行、不要求内部已有该库，也不能直接在 ClickHouse 执行。
ClickHouse 当前对接方向为素材元信息/事实查询，尚未批准它作为任务状态权威。

## 既有清洗系统接入

复用 pipeline 项目的 DJ `plan_flow`，由 `CleaningExecutor` 适配输入清单、
计划、批准摘要及清洗结果。业务 task/revision/branch/generation/operation
与 DJ task/plan/run 显式关联；一次业务任务可以有多次清洗运行。
清洗执行成功不直接发布业务交付，输出文件数量不当作 accepted_count。

本项目负责项目批次、检索补数、跨轮及历史批次去重、授权和交付版本。
DJ 本地文件/SQLite 与工作区所有权仅作为执行器内部能力，
不替代业务账本或内部项目鉴权。算子库/结果中心展示逻辑可复用，
真实接入须对齐受授权的业务 API。该适配尚未完成。

## 检索与素材映射

检索计划绑定业务库、物理绑定、索引版本、编码器版本、维度、距离类型。
`binding_id` 由服务端配置解析到实例、database、collection 和字段名。
一个向量主键必须与物理绑定和索引版本一起解析，不能只凭 vector_id 找素材。
每个命中返回一项映射结果，缺失/冲突不得静默丢失或计入有效数量。

游标绑定授权作用域、计划、索引和查询。适配器明确声明 live/snapshot，
达到检索深度上限与检索穷尽分开返回；内部版本不支持的能力应明确拒绝。
SDK 的进程内 iterator 不能未经验证就当作可恢复的持久游标。
目录、检索、元信息和文件读取都须检查当前权限。

## 内外开发交接

1. 内网提供脱敏字段定义、库版本、主键映射示例、编码器契约及读取/授权 API 约定。
2. 外部基于这些契约制作测试数据，补齐适配实现，保留统一错误及能力声明。
3. 将代码、版本锁定依赖、目标系统对应的安装包/镜像带入内网。
4. 内网注入服务地址、凭据、证书及字段映射，运行真实数据库与接口契约测试。

`config/internal-integration.example.json` 是对接信息模板，仅含环境变量名和
待填映射。目前应用不加载此文件；填写它不会自动启用数据库。
遇到未配置或未支持的内部适配器，后续装配器应启动失败，不得静默回退内存。
