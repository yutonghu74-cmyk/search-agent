# 开放需求理解草稿与能力检查：首个切片

- A / codex/integration / var/worktrees/integration；基线 49d2f9c20420cca4f8ee8a256ec3b11a77628f61；范围 intent_review.py、tools/intent_review.py、tests/test_intent_review.py、本任务卡、integration 交接、contracts/intent-review.md。
- 依据 v1.9 5.1/5.1.1/5.2、7.7/7.8；保留原文，模型只提建议，执行能力由后端注册。未知规则不得丢弃，原文来源/硬软/否定均显示供确认。
- 本轮实现纯校验/审阅层，不调用外部模型、不新增数据库实体、不创建 task/batch/run、不改变旧 QuerySpec@2 接口。CLI 显式标注人工/模型候选解释非自动理解；无解释时全文待澄清。
- 候选解释绑定原文摘要与字符半开区间；未知字段/伪造来源/越界/错误类型拒绝，未覆盖的非空白文本自动补入 unresolved。覆盖不证明语义正确，始终需要用户审阅。
- 后端能力目录按 kind/predicate/operator/value-type/media-type 匹配，并记录验证器、证据和发布状态；模型不得携带执行代码、能力状态或审批。默认没有已评测的开放规则能力，不将 QuerySpec 注册名或固定宽度 smoke 当作已支持。
- 验收：原文/否定保留、硬约束与偏好区分、任意新规则保留、遗漏可见、矛盾 eq 硬规则提示、未发布/无证据能力不自动支持、输出不可执行、摘要可追溯、旧测试不回退。
- 后续依赖：选择并接入受控模型/内部推理端口、业务样本评测、持久理解草稿/澄清迭代、转换到已确认执行规格。不能称第一阶段全部完成。本轮不安装依赖、不运行 DJ、不联网或推送。
