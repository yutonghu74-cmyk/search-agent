# 需求评测与分支产出统计

本工具只读当前本地账本，输出冻结证据、分支统计、人工标注模板及基线对照。不会执行检索、模型调用、批准、补数或更改调度策略。真实任务内容、候选身份和标注文件保存在仓库外私有目录，不提交仓库。

## 运行

使用当前项目Python环境，设置PYTHONPATH=src。入口为tools/evaluate_recall_runs.py：

```sh
python tools/evaluate_recall_runs.py capture --ledger /path/tasks.sqlite3 --tenant TENANT --user OWNER --task-id TASK --output /private/tmp/run.json
python tools/evaluate_recall_runs.py prepare-review --input /private/tmp/run.json --output /private/tmp/review.json
python tools/evaluate_recall_runs.py evaluate --input /private/tmp/run.json --judgments /private/tmp/review.json --output /private/tmp/report.json
```

capture使用SQLite只读模式及同一读事务。校验持久化所有者、当前批准的需求摘要/revision/generation、清洗来源和检索页；逐条核对浏览投影与真实已提交命中，并核对投影完整数量。没有已发布清洗结果、错误代次或损坏投影时失败，不能作为零产出样本。该离线工具不是在线授权API，不能替代真实依赖的当前授权检查。

输出只创建新文件，权限0600；评测目录建议0700。已有文件不会覆盖。没有可信流水记录的调用量、耗时和分支费用保留null，不以规则数或候选数估算成实际调用。

## 统计口径

- 按服务端持久化批次顺序、同批分支ID、命中ID稳定归因。同一候选的后续命中记重复覆盖，不再记首次发现。相同内容只给首个有合格证据的分支一次有效素材贡献，代表素材变化不产生新贡献。
- 每分支输出原始向量命中、未解析/未判定命中、首次候选、重复覆盖、通过/失败/unknown、有效唯一、入选及候补。一个向量映射多个素材时，原始命中仅计一次，候选数可以更大。不同阶段分母不可混用。
- 有效唯一包含入选与候补；任务实际入选/候补/缺口另以business_counts保留，不把分支统计冒充业务权威计数。人工排除、失败和未知不计有效；人工近重复合并后不再计入其被移除的独立内容。
- attribution_complete标记是否仍有未关联判定或未解析/未判定命中。unseen_assessments展示未关联到当前检索源的清洗候选数；这些不能悄悄补进某分支。

## 人工标注与比较

review.json绑定精确evidence_digest、需求和语料指纹。labels按内容摘要填true/false；null表示未标注，永远不会转为负例。允许部分标注，报告judged_positive_coverage和judged_precision仅覆盖已标注样本；corpus_recall始终null，不能宣称语料完整召回率。标签版本变化会产生新的judgments_digest。

```sh
python tools/evaluate_recall_runs.py evaluate --input /private/tmp/proposed.json --judgments /private/tmp/proposed-review.json --baseline /private/tmp/baseline.json --baseline-judgments /private/tmp/baseline-review.json --output /private/tmp/comparison.json
```

比较必须使用相同冻结需求、语料指纹、标注集合和归因规则。capture可用--corpus指定受控语料快照文件，计算文件指纹并标为operator_declared；这不自动证明历史运行使用过该文件，操作者仍须核对索引绑定及运行来源。没有语料指纹时拒绝对照。两侧实际耗时/调用量有记录才输出相应差值。

## 当前范围

工具已可采集已有结果、生成待标注清单和按分支追查损耗。图片VLM的实际HTTP尝试与耗时已接入下述记录；人工相关性标注、10至20条真实需求的完整对照及产出率驱动调度尚需后续完成。本次不根据少量未标注样本自动调整分支优先级。

## 实际请求与耗时

已批准清洗的每个实际执行步骤写入cleaning_usage_steps，ModelRuntime的VLM HTTP边界写入cleaning_usage_http。绑定操作、任务/revision/generation、素材、配方和步骤；不保存请求文本、图片、密钥、地址或模型响应。每次网络重试单独记录，错误尝试也记入。重放完整结果、恢复已完成素材/步骤、组不适用的跳过不新增测量。视频及其他后端未声明支持时仍不能据此报告完整费用。

- http_attempts_observed只计已返回或抛出异常的HTTP尝试，包括限流/服务错误和网络错误，不能等同服务商已计费的推理次数。崩溃窗口的started记录计为pending，实际是否送达未知。
- http_elapsed_ms是网络调用和读取响应的累计时间；detector_elapsed_ms还包含重试等待与步骤处理。它们不是并行流水线端到端延迟。没有计费系数或token账单时不提供货币费用。
- usage_complete要求当前结果所有适用步骤有受支持、完成的测量，且没有未闭合测量。历史缺记录、缓存来自旧版本、记录完成失败及不支持的后端均保留不完整状态，不补造零调用。vlm_calls是完整时的HTTP尝试计数兼容字段，不改变现有业务补数预算口径。
- 分支成本按素材首次发现分支归因；清洗步骤本身可能合并了多个分支输入。保留unattributed_usage_steps，不把缺来源的用量塞给任意分支。calls_per_unique的分母包含有效候补，不能称为每条已交付成本。
- 采集范围明确为published_result_lineage，只覆盖当前已发布结果来源的清洗操作，不含其他代次、未发布操作、扩词或编码成本。对照同时要求usage_scope一致；完整性仅针对这个范围，不代表全任务计费账单。
- 开始记录及每次HTTP前复核批准状态；结束记录仅更新原测量token，不覆盖新代次结果。统计完成写入失败时保留pending并允许已有模型结果继续原检查点流程，不为了日志失败强制重付一次模型调用。仍可能因业务检查点自身失败发生原有重试，计入真实新尝试。

两张表已注册到本地存储格式、迁移源指纹和任务/Chat删除清理路径。重启或隔离迁移保留记录，删除任务时一并清理。在线授权和业务计数仍由原服务控制。

## 可直接核对的人工材料

```sh
python tools/evaluate_recall_runs.py prepare-review --input /private/tmp/run.json --output /private/tmp/review.json --worksheet /private/tmp/worksheet.md
```

JSON新增冻结需求、硬规则/条件、来源约束、素材ID及授权预览接口；Markdown材料链接现有清洗工作区。预览需当前授权，不复制图片或授予权限。材料不展示模型判定作为人工答案，labels保持null；人工在配套JSON填写true/false，不确定留空。标注依然绑定精确输入证据摘要。
