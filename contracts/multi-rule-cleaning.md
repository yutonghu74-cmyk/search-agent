# 多规则清洗内部执行契约 v1

状态：通用框架基础契约；业务批准到执行的正式装配尚未实现。

## 输入

`ApprovedCleaningJob` 由受信业务层在发起人确认正式任务后构造，不接受草稿、模型输出或外部 JSON 直接发起。绑定必须含 tenant、owner、project、task、revision、generation、task_version、operation_id、输入清单摘要和配方摘要。候选只携带逻辑 ID、素材版本与内容哈希；受信媒体读取器再次核验当前授权、素材字节与摘要。

`CleaningRecipe` 记录来源审阅摘要、能力目录摘要、媒体类型、受信检测步骤和全部硬规则。同一检测步骤可为多个规则提供事实。规则仅支持显式 `eq`，软偏好留在排序层，不能当硬过滤。步骤使用逻辑 backend_ref、validator_ref、evidence_ref，不含 Python 模块路径、命令、URL、工作目录或用户注入参数。真实算子发布仍需满足规格书 7.8 的完整清单、模型/参数/资源/评测要求；当前没有默认已发布检测器。

`DetectionResult` 由已登记执行器返回：status 为 ok 时须有匹配验证器及证据引用，facts 是有限标量映射；unknown 没有事实/成功证据。逐候选逐规则输出 pass/fail/unknown。执行异常、无事实、证据不一致为 unknown 或整个作业失败，不能变成 fail 或 pass。所有硬规则 pass 只表示清洗判定通过；有效交付数量仍由业务层在授权、去重、版本检查后计算。

后续 cleaning 窗口在 `src/dsh_retrieval/adapters/cleaning/` 实现通用调度：每项候选一次授权读取/哈希校验，按配方依赖运行受信检测器，相同步骤只执行一次，结果绑定 operation/revision/generation/recipe/input 哈希；提交前再次检查当前授权和版本。测试检测器仅提供合成事实，标 SIMULATED。真实 DJ 仍只有旧的宽度规则独立桥接，不能将本框架标作多规则真实 DJ 联测。
