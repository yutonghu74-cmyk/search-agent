# 第二轮：内部清洗服务与安全回传

2026-10-21，A 总控单窗口集成，当前主工作区 master，基线 cf17b1c5fd5e4218b9801bb82535c0b5555c6dfd。未创建其他窗口/worktree；本轮尚未提交。范围为清洗公共模型/端口、服务、内存账本、DJ 隔离适配和验证工具。不构成 B/C/D 并行角色任务分派。

## 已实现的开发切片

- prepare 展示服务端配置的候选清单摘要与 image-width@1 规则；start 必须由发起人明确 confirm，提交展示版本和摘要。准备不派发 DJ。
- 内部目录按租户/项目解析清单，限制 1–100 张、单文件 10 MB；不接收外部任意 URL/路径或自由配方。内容变更须重新确认。
- Python 3.11+ 业务服务通过 JSON 子进程调用隔离 Python 3.10 DJ 环境，不改 DJ 源码。使用固定 CPU image_shape_filter + image_audit，60 秒执行期限。
- 同 proposal 不同幂等键仍使用同一 operation；传输失败重试沿用 operation。桥接层使用文件锁、输入/配方指纹和稳定 DJ request_id，保存业务与 DJ ID 映射。
- 逐候选返回 candidate_id、asset_version、pass/fail/unknown、原因和证据引用；审计文件验证清单 SHA-256、大小、完整覆盖和规则一致性。执行或证据失败整批记 unknown。
- 内存锁内校验身份、revision、generation、当前 plan_digest 和 operation；结果、当前指针、内存事件一起更新。旧结果不能覆盖当前结果，终态结果不可改写。
- cancel 先提升 generation、撤销提交资格，再尽力中断 DJ；桥接取消墓碑阻止迟到提交。这里只取消清洗操作并暂停任务，不是完整任务取消 API。
- 清洗计数不是有效去重数量：accepted_count=null；DJ 成功不会把业务任务置为 COMPLETED。

内部调用顺序：CleaningService.prepare → start(action=confirm) → poll → results；cancel 需当前 task_version 和幂等键。公共端口定义在 ports.py，冻结数据在 cleaning_models.py。结果尚为单规则切片，不宣称已实现规格 14.6 全量事实协议。

## 验证证据

- `PYTHONPATH=src python3 -m unittest discover -s tests`：48 项通过，其中新增 10 项清洗测试（fake 执行器）。覆盖明确确认、重复提交、输入变更、传输重试、unknown、覆盖/绑定/版本校验、同代次旧计划、撤权和取消后迟到回传。
- `PYTHONPATH=src python3 tools/cleaning_smoke.py`：真实 DJ PASS；三张合成 PNG（宽 16/64/128），阈值 32，pass=2/fail=1/unknown=0；重复 start/poll 不重复发布，业务 RUNNING。
- 原始真实报告 `var/cleaning-smoke.json`，运行目录 `/tmp/d5zvuw7ur`。临时目录可能被系统清理；仅合成数据，非内网联测。脚本需第一轮的隔离环境，本机沙箱 CPU 探测受限时需获准执行。
- 角色检查 `python3 tools/collab_gate.py check --role core --base cf17b1c5fd5e4218b9801bb82535c0b5555c6dfd`：FAIL，期望 codex/task-core，当前 master。未修改检查器/责任表绕过；不能将单测通过表述为协作门禁通过。

## 未完成 / 不可用于生产的边界

业务账本仍为内存，重启恢复 NOT_RUN；没有正式 revision/branch/run、持久 outbox、全量执行环境/模型/成本记录。事件列表不是正式 SSE。固定本地媒体目录不是 W3/内网媒体网关；授权跨系统撤销竞争尚未解决。桥接进程环境清理不是强隔离沙箱。

HTTP 清洗路由、真实检索、去重/补数、结果导出尚未接通；ClickHouse/Milvus/W3 真实联测 NOT_RUN。仅覆盖 A105 等验收的部分开发场景，不能宣称完整验收通过。不安装 PostgreSQL。

下一切片：模拟检索候选 → 当前清洗 → exact-content 去重计数，建立结果集契约；随后接持久账本/恢复，再开放对外流程。多窗口启动前须另行发布已提交基线并分派独立 worktree。
