# 查询分支路由与整组物理准备

`QueryRoutingService` 只接受服务端注入的 `PhysicalQueryPreparer`，复用其任务、项目权限、目录、编码器及能力注册表。

`discover` 读取当前已确认任务，在指定 scope 下发现各分支可用索引。目录必须先授权再匹配介绍；本层再次核对 discover/search 权限。明确 ACCESS_DENIED 的库不进入输出，授权服务故障传播。只提供已注册且精确匹配索引版本/编码器/维度/度量的能力；完整的已配置授权范围内仍无能力时显示 no_available_index。原始查询必须保留。单次选取 1–64 个分支；扩词更多时由调用方明确选子集，每个子集仍含原始查询。每分支目录上限 256 库、每库 64 个绑定、最终 64 个可用索引，超限报错，不静默截断。

`QueryRoutingPreview.routing_digest` 绑定任务 scope、逻辑预览、授权版本、分支顺序、完整 IndexBinding、过滤模板和批次上限。外部展示仅有逻辑标识和模型能力，不含连接地址/凭据。目录无 catalog_version 的现有接口使用候选集合摘要检测变化；不声称实现目录事务快照。

`prepare` 接收该预览和 1–64 个 `QueryRouteChoice(branch_id,binding_id,batch_size)`。调用编码器前重新发现索引并比对摘要，预检所有选择、原始查询保留、重复分支/索引组合和上限。同分支可选择多个库。逐成员调用现有 PhysicalQueryPreparer，让每个索引使用匹配编码器；比较实际绑定/过滤模板，完成后再发现并比对目录、权限、任务版本。任何失败不返回部分计划。最终返回现有 `RetrievalPlanSet`，由后续账本持久化并经正常确认批准。

此模块不写业务状态、不授予批准、不执行向量搜索；发生中途变化时已完成的 embedding 调用不能撤销，后续费用/用量账本须记录实际尝试。来源硬约束仍需验证过的过滤编译器；当前明确拒绝带 source_constraints 的准备，不自行去掉条件。只实现 text→image 现有能力，真实连接配置、模型客户端、API/UI 与调度另见 remaining-roadmap R02–R07。

## 已保存索引配置接线

`build_configured_routing(tasks, projects, configs, authorization, embeddings, capabilities)` 显式装配已保存配置和路由服务。所有参数是受信后端依赖，不允许通过客户端提交编码器/能力注册。

`ConfiguredCatalog` 复用 CatalogRepository 的 `find_libraries` 形状：从当前任务推导租户、发起人和项目，验证任务 scope 与项目权限后读取个人配置；逐库检查 discover/search 后才做描述/别名匹配，并在返回前复验配置、任务与权限。只有明确 ACCESS_DENIED 被过滤，授权服务故障直接报错。未保存配置返回 CATALOG_SELECTION_REQUIRED，不回退管理员库存中的全部库；已保存、同媒体类型且仍获授权的预制库属于本次可选范围。技术绑定保持原样，只有精确匹配已注册 TextQueryCapability 的索引进入选项。

匹配是 NFKC/大小写归一的有限字面排序信号：完整查询匹配介绍，或已有库 ID/显式别名匹配查询；拉丁别名要求词边界，中文别名允许子串。匹配库排在前面，未命中文字但已配置、已授权且媒体类型一致的预制库作为后续候选，不能因为简介未写某个词就断言库内没有对应素材。不解析否定、来源硬条件或模糊语义；候选库不等于素材满足要求。

词表确认后的默认 `smart_authorized` 策略不让用户填写索引或连接信息：在已确认、已配置、已授权且能力兼容的范围内自动纳入可执行的预制索引，同一查询分支可以绑定多个来源；总词/索引组合仍受 64 个成员上限约束，超限明确停止并要求缩小范围。页面主对话不逐词弹索引选择，只在检索节点展示使用的数据源数量和可追溯来源。自动补数图片任务现支持每查询首轮优先一个兼容索引，其余索引保留在批准计划内供补数；已完成补数按有效唯一新增率继续或调整已批准分支。具体范围与证据见 `contracts/recall-strategy.md`，不对未测量分支虚构有效率。

目录可选暴露 `configuration_digest(context)`。路由预览增加 `catalog_config_digest`，摘要 schema 升为 query-routing@2，绑定配置版本和已确认摘要；发现前后、准备前后均复验。即使索引集合不变，重存配置也使旧预览失效。旧内存预览须重新发现；未实现此可选方法的原有目录继续支持，但没有配置版本栅栏。没有新增批准/执行权或跨系统事务保证。

本包仅后端组合与模拟端到端测试；本地配置页面尚未调用该路由、真实 embedding/向量搜索仍待接入，不自动更新现有服务进程。

## 只读路由入口（后续切片）

GET `/v1/retrieval/tasks/{task_id}/query-routing`：仅任务发起人及有项目权限者可读；返回 `QueryRoutingPreview.to_view()`、`execution_enabled:false` 和执行边界说明。可重复指定 `branch_id` 选择最多 64 个分支，须含原始查询；不传则预览所有分支（超过上限明确拒绝）。其他查询参数拒绝，不接受客户端索引能力或权限版本。未装配返回 ROUTING_NOT_CONFIGURED，不启动任何任务。Chat 确认提单后提供“查看选库结果（未执行）”，按分支展示索引/版本/编码器/维度/批次上限，无匹配显式说明。

本地启动在 catalog-source/catalog-ledger 外显式传 `--query-capabilities <受信文件>`。文件结构：`{"schema":"text-query-capabilities@1","items":[{"binding":完整IndexBinding,"media_type":"image","filter_template_ref":"受信模板ID","max_batch_size":20}]}`。每个绑定 ID 唯一，完整绑定包括 library_id/index_version/encoder_ref/vector_dimension/metric；不根据已有库维度自动生成查询能力。最多 256 个注册项，空 items 表示无能力而不是全库可用。文件不包含模型密钥/物理地址，连接和编码器仍另行接入。

`LocalRoutingPreview` 每次请求重新加载能力和本地目录/授权；发现前后检测变化，配置摘要仍由 ConfiguredCatalog 校验。权限适配仅为本地模拟环境，不能替代 W3。未注册/不匹配能力不进入选项；返回 available 仅证明配置匹配，不证明模型端点或向量库健康。内部禁用编码器，即使意外调用也报 QUERY_EXECUTION_DISABLED；没有准备/批准/执行 POST 入口。
