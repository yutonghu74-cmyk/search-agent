# 主标签与辅助检索词分组

`grouped-expansion@1` 将“交付分类”和“召回用词”分开：每个 group 的 `primary_label` 是独立交付单元，`package_name` 必须与主标签相同且 `package_mode=separate`；`auxiliary_terms` 只用于建立后续查询分支，不创建独立结果包，也不单独占用交付数量。

本地预览只识别用户明确写出的 `主标签扩N个词`，N 为 1–30，最多 20 组。例如 `动物，狗扩20个词，猫扩30个词` 生成上级标签“动物”下的狗、猫两组。没有明确语法时返回 `recognized=false`，不从普通自然语言猜测分类；当前 `local-template@1` 只是可审阅的确定性词表组合，不代表模型扩词质量。

每组 `delivery_target=null`，因为“扩 20 个词”不是“交付 20 张图片”。在用户另行确认每组配额前，系统不得平均拆分总目标。`overlap_policy=single_primary_per_aggregate` 表示同一素材即使带多个标签，在聚合总数中默认只计一次；可生成额外聚合清单，但不复制素材文件或重复累计总数。

`POST /v1/retrieval/projects/{project_id}/expansions` 需要可信身份和项目创建权限，只返回待确认预览。正式草稿保存完整分组及 `plan_digest`，篡改标签、辅助词、包名或策略会导致验证失败。预览和确认都不启动检索、清洗或打包。
