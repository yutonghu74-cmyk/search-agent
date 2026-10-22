# Chat 提单与反馈最小契约

状态：P0 本地体验候选。它冻结业务边界，不代表自然语言解析、真实检索或生产身份已经接通。

## 首次提单

首次 Chat 输入仍使用既有项目草稿接口。页面把自由文本放入完整 `query-spec@2` 确认卡；只有用户确认草稿版本和方案摘要后，才创建 task、batch、revision 1 和 PLANNING run。普通发送消息不等于确认。

## 反馈草稿

`POST /v1/retrieval/tasks/{task_id}/feedback` 至少提交：

- `text`：本次追加反馈；
- `scope`：首个体验版只接受 `all`；
- `expected_revision`：反馈所基于的已确认版本；
- `expected_task_version`：保存时的任务乐观锁版本。

服务端从当前已确认完整方案派生候选方案，不能接受调用方直接替换 project、owner、task、revision、generation 或物理数据绑定。首个体验版只把反馈文本明确追加到需求描述，保留其余结构化条件，并在确认卡标注“尚未编译为新的可执行清洗规则”；因此不能把保存草稿或确认文本宣称为已执行对应清洗语义。

保存成功返回 `pending_draft`，至少包含 `draft_id`、`draft_version`、`base_revision`、`base_task_version`、`proposal_digest`、完整 `proposal`、`feedback_text`、`scope` 和 `status`。保存/修改草稿不改变 `confirmed_revision`、`task_version`、`generation` 或当前 run。

## 确认与拒绝

`POST /v1/retrieval/tasks/{task_id}/confirmations` 绑定：

- `draft_id`、`expected_draft_version`；
- `proposal_digest`、`expected_task_version`；
- `action=confirm|reject`；
- `Idempotency-Key`。

确认只接受创建人、当前仍有项目批准权限且仍绑定当前 confirmed revision/task version 的草稿。确认事务发布一个不可变新 revision，更新 `confirmed_revision`，提升 `task_version` 和 `generation`，使旧运行失去提交资格，并让新版本进入 PLANNING。拒绝只把草稿标为 rejected。相同键与正文重放返回原结果；同键异体、陈旧版本或摘要冲突返回 409。

`GET /v1/retrieval/tasks/{task_id}` 同时返回当前 `confirmed_revision`、`pending_draft` 和 `task_version`；`GET /v1/retrieval/tasks/{task_id}/revisions` 返回创建人可读的不可变版本历史。所有读写都重新校验任务所有权和项目权限。

本地体验使用 `GET /v1/retrieval/projects/{project_id}/tasks` 列出当前创建人在该项目内、当前仍有权读取的最多 100 个正式任务。它用于“历史需求”入口，并通过 revisions 展示从方案确认开始的正式历史；确认前逐句草稿属于独立 conversation_message 范畴，当前切片不返回，页面不得把正式版本列表称为完整聊天记录。

## 本地体验边界

- 身份头和 demo 项目授权只供 loopback 本地开发，不可暴露到网络。
- 本地 SQLite 账本用于重启体验，不是生产存储。
- 反馈确认发布需求版本，但不自动声称真实检索、清洗、补数或最终交付完成。
- W3、内部平台项目上下文、真实数据源和模型语义编译均为 NOT_CONNECTED。
