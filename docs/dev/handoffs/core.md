# core 精确内容计数预览交接

- 角色 / 分支：core / codex/task-core。
- 独立 worktree：/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/core。
- 分派任务：docs/dev/core-counting-task.md；规格 7.11、7.14、8.1 局部场景。
- 基线 SHA：b5bc29794087fbf79914f25b1f7fe4cebcd676d6；本切片尚未提交或合并。
- 状态：READY_FOR_REVIEW。
- 修改仅在 src/dsh_retrieval/core/、tests/core/ 与本文件。未修改共享契约、责任表、检查器或旧 DJ。
- 实现：只读 CountingPreviewService；读取当前完整清洗结果，重新验证项目访问/媒体内容/任务版本，pass 按冻结内容摘要去重，稳定代表与目标截取，返回单规则 unique_pass_count/preview_gap。fail/unknown 不计；accepted_count 保持 null；不派发补数、不写业务状态。
- 实际单测：2026-10-21，独立 worktree 内 `PYTHONPATH=src python3 -m unittest discover -s tests`，56 项通过，新增 8 项计数测试。
- 角色检查：`python3 tools/collab_gate.py check --role core --base b5bc29794087fbf79914f25b1f7fe4cebcd676d6`，退出码 0，SCOPE PASS / LOCAL GATE PASS，56 项测试通过；不是全量业务验收。
- 模拟/真实：新增计数测试使用 fake 清洗执行器及固定文件，不声称真实检索联测；基线已有真实 DJ 证据。本工作区未复制/共享可变 DJ 环境，未重跑真实 DJ。
- NOT_RUN：内网 ClickHouse/Milvus/W3、持久化、恢复、性能、跨轮发布。读取末尾复核不等于跨系统原子授权；结果只是带版本的即时预览。
- 后续由 A 冻结模拟检索/媒体授权/跨轮结果集公共契约，再分派后续工作。本切片不是完整业务有效计数或正式结果发布，不会把单个宽度规则冒充全部自然语言业务条件。

## 串联验证补充

新增 tests/core/test_pipeline.py：从保存草稿、发起人确认、准备清洗、明确确认、轮询清洗到去重预览，全部通过服务入口；不直接调用内存仓库的结果发布方法。固定输入和 Worker 均为 SIMULATED，不冒充检索或真实 DJ 联测。

固定 5 个候选，3 pass（其中两个内容相同）/1 fail/1 unknown，得到 unique_pass_count=2、duplicate_count=1、目标 3 的 preview_gap=1，accepted_count=null；input_only 不补数，任务不自动完成。另覆盖 Worker 整批失败、清洗取消、成功后撤权。完整单测现为 60 项通过。

## 待 A 冻结的候选接入契约提案（未实现）

- 原因：当前 LocalCleaningCatalog 由服务配置固定清单，不具备检索结果到不可变清单的发布接口；不能让检索适配器直接改它的 manifests 字典作为正式接口。
- 建议输入：服务端绑定 tenant/project/task/revision/generation，已批准 plan_id/plan_digest，稳定 operation_id，versioned candidate_id/asset_version/media_ref 列表。客户端不得传路径或任意 URL。
- 建议输出示例：`{"manifest_ref":"manifest_fixture_001","input_manifest_digest":"sha256:<实际摘要>","candidate_count":5,"source_mode":"simulated"}`。示例摘要是占位，运行时必须计算。
- 保证：同 operation 同输入重放；异摘要冲突；拒绝旧代次和未授权媒体；仅完整清单可以准备清洗；空召回返回明确空结果，不伪造清洗成功；清洗仍须经过发起人确认。
- 兼容性：保留 CleaningService.prepare 的 manifest_ref 入参；以新清单适配器实现既有 CleaningCatalog.resolve。候选发布能力须另由 A 定义公共端口，不在 core 内私建接口。
- 受影响角色：A 公共契约/装配，retrieval 候选来源与清单发布，cleaning 媒体物化，core 只读消费。
- 验收：固定候选 5 条 → 明确清洗批准 → 真实 DJ → 去重预览；包含重复来源、素材版本变化、跨租户、重试和空召回。不启用范围外补数，不将 simulated 标成内网通过。
- 集成阻塞：本切片尚未提交/合并；当前用户历史授权为建立本地基线，未包含后续自动合并。需要明确本地提交/合并授权后由 A 发布下一基线和 retrieval 任务卡；不推送。
