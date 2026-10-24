# 查询分支路由与整组物理准备

`QueryRoutingService` 只接受服务端注入的 `PhysicalQueryPreparer`，复用其任务、项目权限、目录、编码器及能力注册表。

`discover` 读取当前已确认任务，在指定 scope 下发现各分支可用索引。目录必须先授权再匹配介绍；本层再次核对 discover/search 权限。明确 ACCESS_DENIED 的库不进入输出，授权服务故障传播。只提供已注册且精确匹配索引版本/编码器/维度/度量的能力；不支持的分支显示 no_available_index。原始查询必须保留。单次选取 1–64 个分支；扩词更多时由调用方明确选子集，每个子集仍含原始查询。每分支目录上限 256 库、每库 64 个绑定、最终 64 个可用索引，超限报错，不静默截断。

`QueryRoutingPreview.routing_digest` 绑定任务 scope、逻辑预览、授权版本、分支顺序、完整 IndexBinding、过滤模板和批次上限。外部展示仅有逻辑标识和模型能力，不含连接地址/凭据。目录无 catalog_version 的现有接口使用候选集合摘要检测变化；不声称实现目录事务快照。

`prepare` 接收该预览和 1–64 个 `QueryRouteChoice(branch_id,binding_id,batch_size)`。调用编码器前重新发现索引并比对摘要，预检所有选择、原始查询保留、重复分支/索引组合和上限。同分支可选择多个库。逐成员调用现有 PhysicalQueryPreparer，让每个索引使用匹配编码器；比较实际绑定/过滤模板，完成后再发现并比对目录、权限、任务版本。任何失败不返回部分计划。最终返回现有 `RetrievalPlanSet`，由后续账本持久化并经正常确认批准。

此模块不写业务状态、不授予批准、不执行向量搜索；发生中途变化时已完成的 embedding 调用不能撤销，后续费用/用量账本须记录实际尝试。来源硬约束仍需验证过的过滤编译器；当前明确拒绝带 source_constraints 的准备，不自行去掉条件。只实现 text→image 现有能力，真实连接配置、模型客户端、API/UI 与调度另见 remaining-roadmap R02–R07。
