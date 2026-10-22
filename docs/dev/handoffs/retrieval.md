# Retrieval 检索适配切片

## 当前任务：授权媒体物化预览

- 角色 retrieval；独立 var/worktrees/retrieval；分支 codex/retrieval-adapters；基线 10228c0ed84c45c49133e4f83868bd20af52a4f8。
- 范围 adapters/retrieval/、tests/retrieval/、本交接；验收与边界见 docs/dev/materialization-task.md。只做临时预览，不发布持久候选或启动清洗。
- 2026-10-22 11:16（Asia/Shanghai）：实现 materialization.py，新增 test_materialization.py 的 18 项用例。独立限制单素材/整页字节、流式校验 SHA-256、完整映射及同身份冲突检查、前后权限/任务/映射重查；上下文成功或异常退出删除本次临时目录。相同版本身份保留多命中，不按内容合并跨来源素材。
- `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`：178 项通过；`python3 tools/collab_gate.py check --role retrieval --base 10228c0ed84c45c49133e4f83868bd20af52a4f8`：退出码 0，角色范围与 178 项测试通过。git diff --check 通过。
- 验证仅合成字节 + 模拟媒体/授权网关 + 本地 SQLite。未运行真实 DJ、内网或真实媒体下载；无图片解码、持久候选发布、业务清洗确认装配。跨系统授权不是原子事务，返回后撤权、进程强杀遗留临时目录仍需后续生命周期方案。
- 接口变更：无，消费 A 已发布契约；下一步由 A 定义持久候选快照、批准和清洗任务版本衔接，不能让临时目录充当正式素材仓库。

- 角色：retrieval；分支 codex/retrieval-adapters。
- 独立 worktree：/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/retrieval。
- 分派基线：b2cdedd7b966ad7e4756c127525f81dafd9400fc；当前代码未提交、未合并。
- 本轮任务卡：仅实现现有 CatalogRepository / EmbeddingProvider / VectorSearchGateway / MediaRepository 的确定性模拟适配与契约测试。用户授权继续开发，不自动提交/合并/推送。
- 修改范围：src/dsh_retrieval/adapters/retrieval/、tests/retrieval/、本交接文件。不修改公共模型、责任表、DJ 或其他角色代码。
- 验收条件：规格 6.2–6.7；A03/A04/A12/A20/A37/A44/A45/A49 的适配器级模拟子集：授权前置、编码匹配、冻结请求、稳定分页/深度上限、索引失效拒绝、绑定及版本素材映射、依赖失败关闭。
- 不改变现有公共契约。ApprovedFixture 仅为服务端测试装配数据，不是正式业务批准入口；SIMULATED 不代表实际语义检索。
- 当前状态：READY_FOR_REVIEW（模拟适配切片）；未提交、未集成，不算主分支新增完成模块。
- 完成文件：src/dsh_retrieval/adapters/retrieval/__init__.py、simulated.py；tests/retrieval/__init__.py、test_simulated.py；本交接文件。
- 实际验证：2026-10-22 09:21（Asia/Shanghai），macOS / Python 3.13.3，在本独立 worktree 执行 `python3 tools/collab_gate.py check --role retrieval --base b2cdedd7b966ad7e4756c127525f81dafd9400fc`，退出码 0，SCOPE PASS / LOCAL GATE PASS，104 项测试通过（原 86 + 新增 18）。`git diff --check` 退出码 0；新增文件另外执行空白检查，不用该命令冒充未跟踪文件检查。
- 证据定位：tests/retrieval/test_simulated.py 的 18 个 test_* 方法；本记录为真实执行结果摘要，不是内网验收。链路用例 test_query_to_page_to_resolution_simulated_chain 覆盖目录→固定查询向量→分页→素材映射，但没有装配至正式任务/候选清单。
- 已覆盖：目录授权先于匹配；不同编码器/维度/非有限向量拒绝；批准请求变更拒绝；页重放稳定；空召回；depth_limit 与 exhausted 区分；游标跨计划/实例拒绝；租户/用户/任务隔离；撤权及授权服务故障关闭；索引过期不回到第一页；跨绑定相同主键；映射缺失/冲突/媒体撤权明确隔离。
- 边界：每个 fixture 是固定查询的预排序命中，不运行 ANN、学习型 embedding 或真实过滤表达式。授权服务为注入接口；测试使用可撤权假实现，不是生产权限服务。exhausted 仅指该模拟查询集合，不证明真实全库穷尽。游标仅进程内有效；不支持生产并发保证、重启恢复和业务 generation 栅栏；正式运行不得直接使用 ApprovedFixture 代替用户确认。
- NOT_RUN：真实 ClickHouse/Milvus、模型编码、内网授权、持久化游标、多进程/并发压力、正式候选发布及 DJ 串联。本轮不运行 DSH，不安装依赖。
- 下一步：总控冻结实际检索计划批准/epoch 契约、能力登记及候选发布接口后，接入真实连接器与业务流程。此切片不得标为 P01 全部完成。

## 提交总控的后续契约请求（尚未实现）

- 原因：现有 SearchRequest/AccessContext 没有正式计划审批存储、revision/generation/epoch 绑定；现有候选接入不接受空清单。不能用模拟 fixture 自动批准业务检索，也不能将空召回伪装成检索故障。
- 建议请求语义：以 task_id + revision + generation + plan_id + plan_version + epoch 读取已批准计划，冻结物理绑定、查询及过滤模板；示意 `{task_id: task_1, revision: 1, generation: 2, plan_id: plan_1, plan_version: 1, epoch: 1}`。这只是提案，不是已发布的 API。
- 建议响应语义：返回该作用域的批准摘要与连接器能力；不匹配拒绝，未批准拒绝，过期快照暂停。候选发布须允许零命中并保留 stop_reason，不创建清洗作业、不修改正式有效数量。
- 兼容性/影响角色：A 维护公共模型与装配，core 校验任务/版本与候选发布，retrieval 消费冻结计划，cleaning 仅消费已经批准的非空合法清单；现有接口不在本切片内修改。
- 后续验收：旧 generation/epoch 不得提交；空召回正常结束分支；新索引切换需新 epoch；授权故障不是空结果；正式清洗仍需确认。等待 A 发布新契约基线后实施，不复制另一套公共模型。

## 2026-10-22 同步公共契约后的集成准备

- 用户已授权本地提交及审查后合并；不推送/部署/接内网。
- A 发布并合入主分支的新契约基线：4d666cd57907cc0e4abfa4e914e575469208b5b4；本 retrieval 分支已快进同步。上文旧基线为历史开发记录。
- 新基线任务范围仍限原 retrieval 允许目录和交接；新增两个组合用例，验证实际模拟分页/空结果/深度限制能通过公共 validate_page。未实现 RetrievalPlanRepository，不将 fixture 批准等同业务批准。
- 2026-10-22 10:14（Asia/Shanghai），Python 3.13.3：`python3 tools/collab_gate.py check --role retrieval --base 4d666cd57907cc0e4abfa4e914e575469208b5b4` 退出码 0，SCOPE PASS / LOCAL GATE PASS，123 项通过（原 86 + 公共契约 17 + 模拟适配 18 + 组合 2）。暂存后另做 git diff --cached --check。
- 状态 READY_FOR_REVIEW，准备本地提交/集成；后续原子批准账本/装配仍未实现，P01 未完成。
