# R06 只读检索进度 API 工作包

- 状态：READY_FOR_REVIEW。角色 integration；独立 worktree `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/r06-progress-api`，分支 `codex/r06-progress-api`，可信基线 `e43355f3dee2fe7b31f060792dd40f7e79051b8a`；任务卡 `docs/dev/tasks/r06-progress-api.json`。
- 范围：`api.py`、`retrieval_progress.py`、`tests/test_retrieval_progress_api.py`、多分支契约、路线图、总进度及本交接；不修改 core 账本或已有运行数据库。
- 实现：GET 当前整组分页水位；响应只含经授权的成员元信息、已提交页数、预留待回传/分页停止状态。`pending` 不误称为重试，可能正在请求。服务默认不装配；本地 demo 可以通过显式启动参数接 SQLite 与本地受信目录授权，限定 demo/default 项目。不开 POST 派发。
- 定向：`PYTHONPATH=src:tests:. PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_retrieval_progress_api -q`，最终文案修正后 7 项 PASS；覆盖真实 HTTP 与临时 SQLite、未批准/未装配、发起人身份、库撤权、目录源损坏、旧 generation、重建 reader、响应脱敏、全部分页停止及 CLI 参数边界。协调器/SQLite 联合定向首轮 21 项 PASS。最终 `python3 tools/collab_gate.py package --base e43355f3dee2fe7b31f060792dd40f7e79051b8a --task docs/dev/tasks/r06-progress-api.json` 退出 0，PACKAGE SCOPE PASS、603 项完整回归 PASS、LOCAL GATE PASS；日志 `/tmp/search-agent-r06-progress-package-gate-final.log`。本行仅补实际结果，未更改被测代码；`git diff --check` 退出 0。
- 真实 W3、Milvus/embedding、HTTP 执行、生产租约和自动补数 NOT_RUN；已有 8081/8082 服务不自动启用新入口。
