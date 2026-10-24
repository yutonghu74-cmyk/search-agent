# 私有物理索引注册表（公共契约）

本包由 A 冻结 R02–R05 的配置解析和身份校验，供 C 的真实连接器消费；不新增驱动，不探测内网，不提供创建 collection 或索引功能。

## 模型与文件

`PhysicalIndex` 引用完整 `IndexBinding`，同时要求：connection_ref、database、collection、primary_key_field、vector_field、model_id、model_version、preprocessing_version、filter_template_ref、pagination_mode、read_mode、max_batch_size、max_depth。

- connection_ref 是受信连接注册表的逻辑引用，不是 URL 或密钥；此文件不接收 endpoint、凭据、任意过滤表达式。物理字段仅后端可读，不发给 LLM 或浏览器。
- 分页声明仅接受 single_topk/offset/iterator/snapshot_cursor；读取声明仅接受 snapshot/stable_cursor/live。snapshot_cursor 必须配 snapshot；stable_cursor 只允许 iterator/offset。声明不是部署能力的验证证据。
- max_batch_size 为 1–100，max_depth 至少覆盖单页，最多 1,000,000；维度为 1–65,536，metric 为 COSINE/IP/L2。未覆盖产品能力须新增显式契约，不静默默认。
- 字段 ID 使用最多 128 字符的 ASCII 字母数字及 `_ . @ / -`，不接受任意 SQL 标识符/表达式。实际驱动仍须参数化和验证字段存在。
- 私有 JSON：`{"schema":"physical-index-registry@1","entries":[完整PhysicalIndex对象]}`，binding 是原 IndexBinding 对象。严格字段、重复 JSON 键拒绝、最多 256 项、最大文件 1 MB；错误不回显私有内容。

## 身份与失效规则

`PhysicalIndexRegistry.resolve(binding, expected_config_digest=...)` 必须完整匹配索引/库/版本/编码器/维度/metric。配置摘要覆盖全部物理、模型及读取参数；批准或缓存使用者须固定摘要，后续解析时比较，collection 别名切换也不得复用旧配置。仅使用 binding_id 无法检测重绑定；省略 expected_config_digest 只用于初次配置发现。

相同 encoder_ref 不能登记不同模型/版本/预处理/维度/metric；相同连接/database/collection/vector_field/index_version 不能登记冲突向量空间或主键字段。同物理 collection 的不同业务库与授权过滤模板可以共存。

`vector_location_key` 覆盖完整目标配置摘要及主键类型和值，隔离不同 collection、索引版本及整数 1/字符串 "1"；bool 不作为整数主键接受。此键是映射命名空间，不是素材去重身份。配置变更可保守失效映射缓存；最终去重仍使用授权映射所得素材版本身份，并保留多来源命中。

`query_cache_key` 绑定完整目标配置、原始查询、租户、用户、任务、用途和授权版本；这里只构造键，没有实现缓存。读写缓存仍须实时授权，不能用摘要代替权限，也不能承诺永久有效。

## 真实连接器后续验收

1. 部署版本/collection 实际字段和模型身份预检成功，才可以宣称连接可用；纯 JSON 校验不得标 HEALTHY。
2. 查询输出同维不同模型仍拒绝；实际读取目标与登记不符拒绝。
3. 分页须固定请求/快照/水位；live 不冒充 snapshot，top-k 到上限返回 depth_limit，不推断全库耗尽。当前 SearchPage 仍只有 snapshot/live，stable_cursor 声明不能直接冒充 snapshot，需后续明确驱动映射与证据契约。
4. 素材映射使用完整目标与 vector ID，映射后绑定 asset_version；缺失/冲突不计有效，读取前后检查撤权。

本包未装配至 HTTP、启动预检或真实检索执行；不修改既有 IndexBinding/SearchPage 以绕过未实现的连接器能力。
