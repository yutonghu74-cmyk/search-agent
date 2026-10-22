# 多规则清洗内部执行契约 v1

状态：通用框架基础契约与合成检测器执行器已实现；业务批准到执行的正式装配尚未实现。

## 输入

`ApprovedCleaningJob` 由受信业务层在发起人确认正式任务后构造，不接受草稿、模型输出或外部 JSON 直接发起。绑定必须含 tenant、owner、project、task、revision、generation、task_version、operation_id、输入清单摘要和配方摘要。候选只携带逻辑 ID、素材版本与内容哈希；受信媒体读取器再次核验当前授权、素材字节与摘要。

`CleaningRecipe` 记录来源审阅摘要、能力目录摘要、媒体类型、受信检测步骤和全部硬规则。同一检测步骤可为多个规则提供事实。规则仅支持显式 `eq`，软偏好留在排序层，不能当硬过滤。步骤使用逻辑 backend_ref、validator_ref、evidence_ref，不含 Python 模块路径、命令、URL、工作目录或用户注入参数。真实算子发布仍需满足规格书 7.8 的完整清单、模型/参数/资源/评测要求；当前没有默认已发布检测器。

`DetectionResult` 由已登记执行器返回：status 为 ok 时须有匹配验证器及证据引用，facts 是有限标量映射；unknown 没有事实/成功证据。逐候选逐规则输出 pass/fail/unknown。执行异常、无事实、证据不一致为 unknown 或整个作业失败，不能变成 fail 或 pass。所有硬规则 pass 只表示清洗判定通过；有效交付数量仍由业务层在授权、去重、版本检查后计算。

`recipe_bridge.compile_reviewed_recipe` 从受信编译器重新读取当前审阅草稿与能力目录，要求创建人审阅动作、版本和审阅摘要一致，再把硬规则转换为不可变 `CleaningRecipe`；软偏好不进入硬规则。它不接受外部预览 JSON，也不创建任务、批准作业或发起执行。来源审阅摘要和能力目录摘要保留在配方中；后续业务批准记录还须显式绑定正式 task/revision/generation、候选清单和配方摘要，不能把“审阅过”当作“批准执行”。

`src/dsh_retrieval/adapters/cleaning/` 已有受控通用调度：每项候选一次授权读取/哈希校验，相同步骤只执行一次，结果绑定原作业的 operation/revision/generation/recipe/input 哈希；返回前再次检查当前授权和版本，发布时仍需业务账本原子栅栏。当前配方仅支持独立步骤、`eq` 硬规则；规格书要求的显式步骤依赖/资源上限/失败处理版本化仍待补齐，不能静默运行依赖链。测试检测器仅提供合成事实，标 SIMULATED。真实 DJ 仍只有旧的宽度规则独立桥接，不能将本框架标作多规则真实 DJ 联测。
