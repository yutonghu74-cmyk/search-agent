# A 分派：core 精确内容计数切片

用户已授权本地基线提交与独立分支/worktree；不推送。基线为包含本任务卡的提交，建立工作区后在 core 交接文件记录完整 SHA。

- 角色：core；分支 codex/task-core；独立工作区 var/worktrees/core。
- 修改范围：src/dsh_retrieval/core/、tests/core/、docs/dev/handoffs/core.md。不得修改公共契约或旧 DJ。
- 本轮仅实现只读、单个完整清洗 operation 的精确内容去重预览，不作为发布结果集或跨轮账本。使用既有 CleaningRepository、CleaningCatalog、ProjectContextProvider；不新增公共端口。
- 服务输入为 actor/task_id；读取当前清洗结果、候选冻结摘要、已确认 QuerySpec。当前授权与媒体内容再次验证；读取前后核对任务版本与当前 operation，冲突拒绝返回。
- 只支持 exact-content@1、cross_batch scope=none、input_only。按 content_digest 分组，仅 pass 可成为代表；按 candidate_id/asset_version 稳定选代表，无偏好/质量/召回排序，不宣称完整 SelectionPolicy。
- 输出清洗合格数、重复淘汰数、单规则唯一通过数、目标和缺口，显式 preview_only=true、accepted_count=null。单个宽度规则不足以证明自然语言业务需求全部满足，不能发布正式 accepted_count 或完成任务。
- 验收：重复不计、fail/unknown 不计、稳定代表、上限截取、重放一致、输入变更/撤权/代次切换拒绝、未完成清洗拒绝、无任务状态写入。规格 7.11、7.14、8.1 的局部验证，不宣称全量通过。
- 模拟检索、跨轮计数、发布事务、持久化、补数不在本轮 core 职责内；后续由 A 冻结契约再分派。
