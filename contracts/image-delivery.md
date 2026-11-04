# 本地图片交付切片

范围：单组图片，以及已确认组配额（R14）的多组图片；exact-content@1、人工打包批准。支持目标已满足的完整交付，也支持自动补数因预算、连续无进展或策略耗尽而合法停止后的非空部分交付。最多 100 张且 ZIP 不超过 64 MiB；未确认各组目标的多组任务、空结果交付和自动发布明确不支持，不代表生产内网或 v1.9 全量验收。

- `GET /v1/retrieval/tasks/{task}/delivery`：读取当前版本待发布/已发布包；无包返回 `available=false`。
- `POST .../delivery/prepare`：传 `expected_task_version`、`result_set_digest`；事务外读取并校验入选图片，生成固定顺序 ZIP 和 manifest.json，短事务重新校验输入/权限/版本后保存包并进入 `AWAITING_INPUT / FINALIZING`。本接口不执行模型、不开放下载。
- `POST .../delivery/publish`：传 `package_id`、`manifest_digest`、暂存版本 `expected_task_version`、`action=confirm`。仅发起人可确认。重验当前规则、成员、权限、媒体与 ZIP 摘要后，以新 delivery result ID 原子发布并置 `COMPLETED`。
- `GET .../delivery/{package_id}/download`：仅当前已发布包；每次检查当前权限、版本和媒体摘要，返回私有、禁止缓存的 ZIP。撤权、取消、换代、损坏均拒绝；不使用永久公开地址。

同一预览的重复生成返回原包；相同包与暂存版本的重复批准返回已发布结果，不重复写发布事件。新需求/暂停/取消先取得控制权时，旧提交失败。历史包留存但本切片不提供旧版本下载入口。

SQLite 增加 `image_deliveries`（不可变 ZIP 和状态元数据）、`image_delivery_events`（同事务 outbox）。已有清洗账本格式不变。读取清洗预览允许同源 staged/delivery 的受控版本延续；写入校验不放宽。预览 ID 不变，正式交付生成新 ID，不能把预览成员标为已交付。现有删除会话流程同事务清理包及事件。

清单包含入选成员、文件名、素材版本与 SHA-256、确认需求摘要、规则、模型绑定（若存在）、来源及许可元数据（本地语料）、去重/选样策略。`eligible_unique_count` 记录去重后合格唯一数，`exact_duplicate_count` 记录结果版本累计自动合并的 SHA-256 精确重复数，`packaged_file_count` 必须等于入选数及 ZIP 内 `images/` 文件数。SHA-256 完全一致的内容只打包稳定代表副本；`candidate_ids` 保留同内容候选别名，`retrieval_sources` 保留跨词、跨库、跨轮命中证据，不能因只输出一个文件而丢失来源。新交付只接受 `exact-content@1`，pHash 近重复和人工重复簇裁决暂不启用；历史清单缺少新增汇总字段时只在界面按已有计数、成员别名和文件列表推导，不回写或改变旧摘要。候补不进入 ZIP；业务数量不从文件数量反推。下载后文件无法技术性召回。

此处 outbox 仅为本地交付审计/恢复记录，不发送训练平台事件或授权下游消费；导出用途授权由媒体网关的 `delivery_export` 用途显式控制。


## 多组配额交付（R18 切片）

已确认 `delivery_target` 的任务按组打包，清单为 `image-delivery@3`。每个入选素材按结果中的 `assigned_group_id` 放入 `images/<序号>-<主标签>/NNNN.jpg|png`（序号按方案分组顺序，主标签中不安全字符替换为 `_`）；各组内文件编号从 0001 起。清单 `groups` 冻结每组 `group_id`、标签、目录、目标、入选数、可计入该组的候补数、缺口与包内文件数，并要求文件数等于结果中该组入选数；成员带 `group_id`。总 `gap` 仍为各组缺口之和，任一组不足即 `completion=partial`，只在补数合法停止后可发布，与单组部分交付门禁相同；入选为 0 的组保留在清单中但无文件。未确认目标的多组任务仍拒绝打包，因为无法判定素材所属目录。读取与下载时按清单逐组校验文件名与摘要；结果与包成员按集合比对，不依赖顺序。旧 `image-delivery@2` 包校验不变。
