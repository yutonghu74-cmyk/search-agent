# 文本登记已有索引（R02 后端切片）

用户选择“文本描述生成并保存已有向量库与索引的配置”，不是创建物理资源或向量化入库。
本包提供 `TextCatalogConfigService`，不提供公开 HTTP/UI，不替代 C 的生产目录适配器。

## 受信输入与流程

- 服务装配：独立 SQLite 文件、已有 ModelRuntime、`inventory(scope) -> tuple[Library, ...]`、实时 `authorize(scope, action, library_id_or_None) -> bool`。异常传播，只有显式 True 放行。
- scope 是认证后端构造的 tenant_id/owner_id/project_id，禁止直接信任请求体。configure 为项目配置权限，discover/search 为库权限；不能把普通任务创建权限当配置管理权限。
- 预览 `preview(scope, text, llm_id)`：先检查项目权限，再逐库授权，仅授权后的业务介绍及索引 ID 进入模型。描述上限 4000 字、候选上限 256 库；模型整理最多 32 库，每库描述及 30 别名、64 索引。未知、跨库索引和额外字段拒绝；空选择不默认为全库。
- 返回 `{proposal, proposal_digest}`，proposal 包含 schema、scope、base_version、catalog_digest、entries。entries 形如 `[{"library_id":"animals","description":"猫狗图片","aliases":["宠物"],"binding_ids":["animal-image-v1"]}]`。模型前后复验配置及目录，预览不持久化，不创建 task/batch/run。
- `save(scope, proposal, proposal_digest)` 是明确确认入口：重新授权、校验完整摘要及当前技术目录；SQLite 事务比较 base_version 原子保存，返回新版本。重复相同确认返回相同版本；旧版本不得覆盖新配置。摘要用于内容一致性，不是权限凭据或签名，授权用户可以修改候选但仍须通过全部校验。
- `read(scope)` 重启可读，隔离租户/用户/项目；目录变化或权限撤销失败关闭，需重新生成确认。`configured_libraries(scope)` 返回复用既有 Library/IndexBinding 的配置结果，别名并入路由描述，不扩大物理范围或权限。

## 边界与后续

物理连接、database/collection、索引维度、编码器和过滤模板必须由管理员受信配置/目录适配器提供。LLM 不生成这些执行参数，不接收凭据；调用方也必须避免把凭据或内网地址粘贴到文本。自由文本会发送给所选 LLM，不保证能够自动识别所有敏感内容。

当前配置按个人项目隔离，是本地持久化后端，不是生产共享目录。目录摘要用于漂移检测，不提供跨授权服务/目录/SQLite 的分布式事务；执行端仍须逐批授权与版本校验。配置语义描述不是实际过滤条件，也不证明连接可用或向量空间兼容。

下一包：配置管理 HTTP/UI、真实受信索引清单加载、配置到 QueryRoutingService 的 CatalogRepository 装配；应保留现有实时任务/项目权限检查和 TextQueryCapability 精确匹配。真实模型质量、ClickHouse/Milvus、端到端搜索均未在本包验证。
