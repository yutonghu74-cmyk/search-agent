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
- 列表摘要包含首条消息的 `created_at`，供历史 Chat 显示精确到分钟的发起时间。仅没有任何
  `ConversationConfirmation` 的会话允许创建人删除；存在确认关联即视为已进入正式任务边界，
  `DELETE /v1/retrieval/conversations/{conversation_id}` 必须返回冲突且不得删除任务或检索记录。
- 本地开发提供显式硬删除覆盖路径 `DELETE /v1/retrieval/conversations/{conversation_id}?force=true`：
  仍按创建人/项目/维度 `create` 权限校验，但绕过上述确认冲突保护，在同一事务内级联删除该 Chat
  的消息、确认关联、关联正式 task 及其 revision/batch/feedback/draft、以及 task 作用域的检索、
  候选 manifest、plan_set、多规则清洗账本与 `candidate_browse_hits` 投影行。各账本按 task_id 过滤后
  重建其 `load_state`/`restore` 强制校验的事件不变量，避免下次读取触发 STORAGE_CORRUPT。这是 v1.9
  P2 异步保留/清理作业的本地同步子集，不实现保留期限、并发会签或生产异步队列；默认 `delete_conversation`
  守卫保持不变，未带 `force=true` 仍返回冲突。
- 早期无 `conversation_id` 的遗留正式 task 没有可删除的 Chat，提供等价的显式硬删除路径
  `DELETE /v1/retrieval/tasks/{task_id}?force=true`：仍按创建人/项目/维度 `create` 权限校验，在同一事务内
  级联删除该 task 及其 revision/batch/feedback/draft 与 task 作用域的检索、候选 manifest、plan_set、
  多规则清洗账本与 `candidate_browse_hits` 投影行，各账本按 task_id 过滤后重建事件不变量。带
  `conversation_id` 的 task 拒绝该路径（`TASK_HAS_CONVERSATION`），必须经会话硬删除路径处理，避免留下
  悬空确认关联。未带 `force=true` 的 task 删除返回 `NOT_FOUND`，不提供非强制 task 删除。
- 一个 Chat 最多对应一个正式 task；同一 Chat 可包含多个检索分组，并可通过反馈确认产生该
  task 的多个 revision。新的独立需求单必须由“新建 Chat”创建，不能在旧 Chat 内生成第二个 task。

消息正文不能作为执行依据。执行只读取已确认的不可变方案快照；对话记录缺失、过期或按策略删除，不得改变既有正式方案。

已确认任务的 `/followup-intent` 用户消息及模型回复写在独立的 `task_dialogue_turns` 账本。读取已授权的
`GET /v1/retrieval/conversations/{conversation_id}` 时，响应附加最近最多 100 轮的
`task_messages`，按用户、助手顺序返回；普通历史 Chat 打开与任务直接恢复都要展示它们。
没有持久化的发送或未确认扩词结果不得伪装成历史消息或正式 revision。

页面在正式 task 创建前就常驻展示六阶段流程：诉求澄清→扩检索词→检索→清洗→补数→打包。
未确认时只根据 Chat 阶段高亮诉求澄清或扩检索词，后续节点明确显示未开始；任务确认后切换为持久化的真实节点状态，不伪造执行进度。
节点必须明确显示“进行中 / 已完成 / 未开始”等状态；已完成节点允许展开查看该节点的输出。
输出只读取当前已确认诉求、检索词、候选或权威结果视图；若后端尚未发布该阶段的独立输出，
页面必须明确说明“尚未发布”，不得用推测内容代替。

## 未实现与不声明

本阶段不定义 assistant 消息、附件、图片临时引用、会话分享、全文搜索、保留期限、删除并发、HTTP 路由或页面呈现。未验证 PostgreSQL、真实业务数据、内网、W3 或 Data-Juicer。
