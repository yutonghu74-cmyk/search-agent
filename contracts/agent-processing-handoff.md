# 清洗 Agent 到既有过滤执行器的桥接

本片接通后端 `AgentProcessingService.prepare`：从持久化的 `submit_processing_plan` 回执解析处理计划，编译已确认规则对应的发布配方，在原多规则账本准备业务确认提案。随后复用既有确认、候选授权读取、执行检查点、结果发布及业务计数，不新建一套可执行清洗模型。

## 装配与调用

- `AgentProcessingHandoffStore` 为业务端口，快照 `ProcessingHandoff` 复用 `AgentStageTask`、`Task` 和 `ProcessingPlanProposal`，不携带厂商 SDK 对象。
- 开发实现 `SQLiteAgentProcessingHandoffs(journal=..., cleaning=..., authority=..., context_provider=...)` 要求同一任务数据库。授权和上下文回调为服务端有界读取，不执行网络作业或修改任务；上下文必须包含当前 `manifest_ref`，并进入阶段 `input_digest`。
- `AgentProcessingService(handoffs=..., registry_provider=...).prepare(actor, handle, manifest_ref=...)` 返回现有 `CleaningApprovalProposal`；registry_provider 返回可信发布注册表，不接收用户/模型提供的模块、地址或解释器。
- 原 `cleaning.confirm` 批准业务执行，原 `run_approved_multi_rule` 或 `ConfirmedCleaningService.execute` 执行。准备不读取媒体、不调用检测器，也不代替业务确认。本片没有增加公开 HTTP/前端入口或自动清洗 Worker。

## 编译与支持范围

当前桥接仅支持 `filter`。所有需求都必须由清洗 Agent 提案恰好覆盖一次，并通过当前发布能力检查；每个选择还必须与已确认 `rule_ref`、group_id 及配方中的完整 DetectionStep 对应。用途可用不表示该检测器能替换另一条用户规则。配方由既有 `compile_confirmed_recipe` 构造，Agent 步骤顺序不能替换执行顺序/冻结指令，也不能删掉未在 Agent 需求中重复列出的其他硬规则。

`tag/rank/analyze` 在解析检测注册表前明确拒绝 `AGENT_PROCESSING_OUTPUT_UNSUPPORTED`，包括混合筛选与打标的任务。后续需要类型化输出执行、标签账本和交付适配，不能用旧过滤通过/失败表示标签。已发布能力到规则映射不一致同样拒绝，不自动降级执行。

## 来源、确认与恢复

加载回执时重验 owner/project、正式任务版本/generation、当前批准阶段、输入摘要、当前能力和提案摘要。仅完成报告不能编译。准备参数必须等于阶段输入中的候选清单；不能用另一份可访问清单替换 Agent 分析过的输入。

保存通过原多规则事务，再次加载同一回执并重编译发布配方，校验当前清单与来源权限。业务提案和 `agent_processing_handoffs` 来源记录同事务提交，阶段/尝试/回执来源不可覆盖。同一阶段的重复与并发准备只有一个业务提案；生成同一业务摘要的另一阶段不会静默覆盖来源，需重新准备有新版本的阶段。来源表显式登记存储格式，任务/对话硬删除级联清理。

原业务确认入口在递增 task_version 前检查当前阶段头、完成尝试、任务范围和回执摘要。包含 agent_requirements 的新模式任务不能经旧 prepare 绕过来源检查。老任务不要求新来源记录。已生效的业务确认重试遵守原业务幂等语义；确认后由业务状态控制执行，不由规划会话追溯撤销。

执行仍校验发布物、批准输入和有效代次。结果/预览重试复用原检查点与结果；unknown 保持待处理，不计入通过。规划回执不会直接发布有效数量。

## 验证与剩余工作

新增测试覆盖持久回执到真实既有执行器入口、保留其他硬规则、显式确认和重复执行、候选清单绑定、跨用户/来源权限、编码阶段对应的注册表解析期间改版、能力消失、错误规则检测器、发布物变化、标签拒绝、并发准备、未知判定及删除级联。媒体和检测结果使用合成夹具，只验证机制，不代表真实图片模型质量、内网联测或 T06 完整验收。

仍未接通标签结果生产/交付、真实检索/清洗自动派发、前端操作卡和正式任务准入。正式 TaskService 新模式门禁保持关闭；本片不作为解除门禁的依据。
