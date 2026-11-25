# 历史批次精确去重（首期）

对应 v1.9 第12.11节的首期实现。默认 `cross_batch_dedup.scope=none`；用户显式选择后使用 `selected_batches`，比较方法仅 `exact-content@1`。本批内部去重始终独立生效。

## 选择与确认

- `GET /v1/retrieval/projects/{project}/historical-batches?dimension_id=default` 返回 `options` 与 `scope=owned_delivered_images`。仅列出本人、同项目、同子维度的正式 published 图片交付；草稿、staged交付、当前批次不可作为排除基准。
- `POST /v1/retrieval/projects/{project}/historical-dedup`，请求严格为 `{dimension_id, references}`，只解析选择、不创建任务或确认。空列表返回 none。
- 每条 reference 固定 `task_id,batch_id,delivery_id,snapshot_id,manifest_digest,task_version`。最多100条不同交付；返回的 policy 含 `references`、`comparison_methods` 和对有序引用清单计算的 `reference_digest`。
- 草稿保存和需求确认均校验范围；确认前来源版本变化返回 `HISTORY_REFERENCE_CHANGED`。确认后的历史新增交付不改变已冻结基准；确认重试保持幂等。
- UI 更多设置支持读取、多选、应用与清除，确认卡显示选中快照数量。修改选择后须重新审阅确认卡，不由系统自动选历史。

## 计数与审计

清洗合格候选在最终计数前按内容 SHA-256 与冻结交付清单比较。命中项从入选/候补集合排除，优先提升合适候补，再按业务有效去重数重新计算缺口和补数。补数、累计合并、人工复核和重建均经过同一基准；人工放行清洗规则不能绕过历史排重。同一内容命中多个历史交付只扣一次。

历史排重独立于规则失败、批内重复和用户排除。结果绑定 `historical_reference_digest`；`historical_exclusion_count` 是内容数量。`historical` 分类展示 `historical_items`，淘汰阶段为 `historical_deduplication`，原因 `HISTORICAL_EXACT_DUPLICATE`。清洗仍先执行，本实现不承诺节省模型调用。

基准删除或失去使用范围返回 `HISTORY_REFERENCE_UNAVAILABLE`，不会悄悄降级为不排重。内存后端不承接该持久化能力。

## 存储兼容与范围

none 策略保持既有结果摘要算法；历史绑定使用新的摘要封装。旧预览缺失新增可选字段时按 None/空元组解码。分类读取新增第七个 historical 投影；存量库上线前应使用 `tools/migrate_task_asset_bindings.py SOURCE TARGET` 在独立副本重建并验证，再按现场切换流程替换，禁止清空账本。缺投影返回 `READ_PROJECTION_REQUIRED`。

本期未覆盖：跨所有者/跨子维度选择、上一批/全部快捷项、自然语言批次解析、历史来源身份去重、历史近似图片、视频和项目在途批次互斥。不能据此宣称 v1.9 全量验收或全项目绝对无重复。
