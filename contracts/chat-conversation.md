# 草稿会话记录契约（第一阶段）

## 边界

`conversation_message` 是独立的草稿会话账本，不是业务执行事件。确认前的需求输入和确认后的反馈讨论可以保存并恢复，但不会创建正式 task、revision、branch、batch 或 run，也不会出现在正式业务时间线。正式时间线仍从“方案已确认”开始。

本阶段只冻结公共模型和存储端口。持久化、服务/API/UI、保留与删除策略在后续独立任务实现；现有页面在这些后续切片完成前仍只展示已确认任务历史。

## 记录

- `ConversationMessage` 绑定 tenant、owner、project、conversation、draft、单调正整数 sequence、消息类型和时区时间。当前只接受用户的 `request`、`feedback`，不保存或展示模型内部思考。
- 消息正文为 1—4000 字符；公开视图返回用户正文，但不返回 tenant/owner 存储字段，也不携带正式 task/revision。
- `ConversationConfirmation` 是独立确认关联：绑定 conversation、draft、正式 task、正整数 revision 与确认时间。正式身份只通过该记录关联，不回写为逐消息执行事件。
- `ConversationSnapshot` 的消息必须属于同一 project/conversation，序号唯一且递增；确认关联必须属于同一 conversation。
- `Draft.conversation_id`、`Task.conversation_id` 为可选兼容字段；旧构造调用继续得到 `None`。

## 原子性与权限端口

- `TaskRepository.save_draft(..., conversation_message=...)` 必须在同一事务保存/更新草稿及对应消息；任一校验失败不得留下单边记录。
- `save_feedback_draft(..., conversation_message=...)` 遵循相同边界，消息和完整提案草稿同事务保存。
- `confirm_draft` 与 `confirm_feedback_draft` 在现有 task/revision/idempotency 事务内写确认关联；重复提交返回同一正式对象和关联，不产生重复消息、任务或 revision。
- `get_conversation`、`list_conversations` 每次按 actor 重新校验创建人、租户和当前项目访问权。跨租户、跨创建人、项目撤权及未知标识对调用方表现为不可见；列表上限 100。

消息正文不能作为执行依据。执行只读取已确认的不可变方案快照；对话记录缺失、过期或按策略删除，不得改变既有正式方案。

## 未实现与不声明

本阶段不定义 assistant 消息、附件、图片临时引用、会话分享、全文搜索、保留期限、删除并发、HTTP 路由或页面呈现。未验证 PostgreSQL、真实业务数据、内网、W3 或 Data-Juicer。
