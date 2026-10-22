# 精确内容去重结果预览契约（开发切片）

`build_exact_result_set(approved_job, complete_result, target_count)` 只处理 `image`、固定 `input_only` 输入。它要求结果与已批准作业的绑定、候选及规则完整一致；只有全部硬规则 `pass` 的素材才进入精确内容哈希去重。相同内容摘要按候选 ID、素材版本的稳定顺序选一个代表；代表再按稳定顺序取目标内 `accepted`，余量列为 `reserve`。`fail`、`unknown` 和重复副本各自计数，均不计入 `accepted_count`。不足目标时给出 `gap` 和 `SEARCH_STRATEGIES_EXHAUSTED`（仅表示本固定输入用尽）；达到目标时为 `TARGET_MET`。

输出绑定清洗结果摘要、operation、generation 和 task_version，生成稳定结果集摘要。它是纯函数预览，不能自行证明当前授权、任务版本、跨轮/近重复关系、排序偏好或真正的检索策略穷尽；不持久化、不发布交付版本、不派发补数。生产发布必须重新读取当前账本与授权并在业务事务内复核。
