# 查询分支路由与整组物理准备

`QueryRoutingService` 只接受服务端注入的 `PhysicalQueryPreparer`，复用其任务、项目权限、目录、编码器及能力注册表。

`discover` 读取当前已确认任务，在指定 scope 下发现各分支可用索引。目录必须先授权再匹配介绍；本层再次核对 discover/search 权限。明确 ACCESS_DENIED 的库不进入输出，授权服务故障传播。只提供已注册且精确匹配索引版本/编码器/维度/度量的能力；不支持的分支显示 no_available_index。原始查询必须保留。单次选取 1–64 个分支；扩词更多时由调用方明确选子集，每个子集仍含原始查询。每分支目录上限 256 库、每库 64 个绑定、最终 64 个可用索引，超限报错，不静默截断。

`QueryRoutingPreview.routing_digest` 绑定任务 scope、逻辑预览、授权版本、分支顺序、完整 IndexBinding、过滤模板和批次上限。外部展示仅有逻辑标识和模型能力，不含连接地址/凭据。目录无 catalog_version 的现有接口使用候选集合摘要检测变化；不声称实现目录事务快照。

`prepare` 接收该预览和 1–64 个 `QueryRouteChoice(branch_id,binding_id,batch_size)`。调用编码器前重新发现索引并比对摘要，预检所有选择、原始查询保留、重复分支/索引组合和上限。同分支可选择多个库。逐成员调用现有 PhysicalQueryPreparer，让每个索引使用匹配编码器；比较实际绑定/过滤模板，完成后再发现并比对目录、权限、任务版本。任何失败不返回部分计划。最终返回现有 `RetrievalPlanSet`，由后续账本持久化并经正常确认批准。

此模块不写业务状态、不授予批准、不执行向量搜索；发生中途变化时已完成的 embedding 调用不能撤销，后续费用/用量账本须记录实际尝试。来源硬约束仍需验证过的过滤编译器；当前明确拒绝带 source_constraints 的准备，不自行去掉条件。只实现 text→image 现有能力，真实连接配置、模型客户端、API/UI 与调度另见 remaining-roadmap R02–R07。

## 已保存索引配置接线

`build_configured_routing(tasks, projects, configs, authorization, embeddings, capabilities)` 显式装配已保存配置和路由服务。所有参数是受信后端依赖，不允许通过客户端提交编码器/能力注册。

`ConfiguredCatalog` 复用 CatalogRepository 的 `find_libraries` 形状：从当前任务推导租户、发起人和项目，验证任务 scope 与项目权限后读取个人配置；逐库检查 discover/search 后才做描述/别名匹配，并在返回前复验配置、任务与权限。只有明确 ACCESS_DENIED 被过滤，授权服务故障直接报错。未保存配置返回 CATALOG_SELECTION_REQUIRED，不回退全库。技术绑定保持原样，只有精确匹配已注册 TextQueryCapability 的索引进入选项。

匹配是 NFKC/大小写归一的有限字面召回：完整查询匹配介绍，或已有库 ID/显式别名匹配查询；拉丁别名要求词边界，中文别名允许子串。不解析否定、来源硬条件或模糊语义，不保证召回率；用户应补充有用别名。匹配结果只是候选库，不等于素材满足要求。

目录可选暴露 `configuration_digest(context)`。路由预览增加 `catalog_config_digest`，摘要 schema 升为 query-routing@2，绑定配置版本和已确认摘要；发现前后、准备前后均复验。即使索引集合不变，重存配置也使旧预览失效。旧内存预览须重新发现；未实现此可选方法的原有目录继续支持，但没有配置版本栅栏。没有新增批准/执行权或跨系统事务保证。

本包仅后端组合与模拟端到端测试；本地配置页面尚未调用该路由、真实 embedding/向量搜索仍待接入，不自动更新现有服务进程。
