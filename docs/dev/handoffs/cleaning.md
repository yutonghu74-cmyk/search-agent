# Cleaning 角色交接：通用多规则执行框架

- 角色 / 分支 / worktree：cleaning / `codex/cleaning-adapter` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/cleaning`。
- 切片与验收：`docs/dev/cleaning-multi-rule-task.md`，v1.9 7.3、7.7、7.8 与 A09/A14 的框架级子集。分派基线 `2673bc02d65ab54c9e48acf6823d43b6f2e18dda`；本交接在提交前记录。
- 状态：READY_FOR_REVIEW，须由 A 审查后集成；不代表真实 DJ 多规则通过。
- 修改：`src/dsh_retrieval/adapters/cleaning/__init__.py`、`runner.py`、`tests/cleaning/__init__.py`、`test_generic_runner.py`、本交接。
- 契约：消费 A 发布的 `multi_rule_contracts.py`，没有共享接口变更。只接受 `ApprovedCleaningJob`；调用方注入当前批准/权限/版本 guard、受控逻辑媒体 reader、登记检测器映射。无内置检测器、脚本路径、动态 import 或用户参数执行。
- 执行语义：预检登记身份；每候选前后重查当前权限/版本，只读一次并校验 SHA-256；每步骤每候选运行一次，两个硬规则可复用同一检测事实；逐规则和逐素材输出 pass/fail/unknown。缺事实、验证器不一致、检测异常为 unknown；媒体变化、超限及授权/依赖 `ServiceError` 中止作业，不返回部分结果。整体 fail 优先于 unknown，但 unknown 永不算 pass。输出保留原 binding，不含有效去重数量。调用方在持久化发布时仍需再做原子 fencing 校验。
- 模拟与真实：9 项用例只用合成字节和合成检测器，属于 SIMULATED 框架测试。真实 DJ 当前仍只有旧单规则宽度桥接；本模块没有接入 DJ plan_flow、OCR/水印模型或真实清洗任务调度。
- 2026-10-22 16:56（Asia/Shanghai），Python 3.13.3：`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/cleaning -q` 退出码 0，9 项通过；`python3 tools/collab_gate.py check --role cleaning --base 2673bc02d65ab54c9e48acf6823d43b6f2e18dda` 退出码 0，SCOPE PASS / LOCAL GATE PASS，308 项通过。输出中的 LOCAL GATE FAIL 是现有负例测试产生，不是本轮门禁结果。`git diff --check` 退出码 0（新文件另行检查）。
- NOT_RUN：真实 DJ 多规则、真实素材/内网、授权服务、持久发布、恢复/并发及性能。后续需要 A 装配业务批准→作业、结果持久化与原子 fencing，登记真实算子及其评测证据；不得把当前结果计入业务 accepted_count。
