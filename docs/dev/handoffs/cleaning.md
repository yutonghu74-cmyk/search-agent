# Cleaning 角色交接：通用多规则执行框架

## 当前切片：批准作业的受控输入读取（2026-10-22）

- 角色 / 分支 / worktree：cleaning / `codex/cleaning-adapter` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/cleaning`；基线 `5d72dd5ab5f7b4949280ff2ee1da62b8b9a737a8`。修改范围仅 `src/dsh_retrieval/adapters/cleaning/`、`tests/cleaning/` 与本交接。
- 验收边界：v1.9 7.3、7.7、7.8、7.10 和 A14 的局部执行安全要求。`ApprovedManifestAccess` 消费业务仓库的 `get_current` 和当前清单，向通用执行器同时提供 guard/reader；绑定必须与当前批准作业完全一致，候选必须属于冻结清单。授权媒体引用仅来自清单，流式读取有单素材上限，读前读后重查当前批准与权限。执行器另校验内容摘要及作业总上限。
- 修改文件：`src/dsh_retrieval/adapters/cleaning/approved_input.py`、`__init__.py`、`tests/cleaning/test_approved_input.py`、本交接；没有公共契约变更。
- 合成证据：`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q` 于 2026-10-22 运行，330 项通过。用例覆盖批准清单读取、伪造绑定/候选拒绝、撤权/换代、媒体变化/超限、读取中换代；均为 SIMULATED，不是真实 DJ 或内网媒体联测。
- 未完成：未登记真实多规则检测器或 DJ plan_flow；未建立后台派发、超时/重试、结果原子持久化与跨轮业务计数。当前返回判定不能直接算 accepted_count。下一步由 core 实现返回结果的当前代次原子 fencing 与完整性校验，再由 A 装配受控 Worker。

- 角色 / 分支 / worktree：cleaning / `codex/cleaning-adapter` / `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/cleaning`。
- 切片与验收：`docs/dev/cleaning-multi-rule-task.md`，v1.9 7.3、7.7、7.8 与 A09/A14 的框架级子集。分派基线 `2673bc02d65ab54c9e48acf6823d43b6f2e18dda`；本交接在提交前记录。
- 状态：READY_FOR_REVIEW，须由 A 审查后集成；不代表真实 DJ 多规则通过。
- 修改：`src/dsh_retrieval/adapters/cleaning/__init__.py`、`runner.py`、`tests/cleaning/__init__.py`、`test_generic_runner.py`、本交接。
- 契约：消费 A 发布的 `multi_rule_contracts.py`，没有共享接口变更。只接受 `ApprovedCleaningJob`；调用方注入当前批准/权限/版本 guard、受控逻辑媒体 reader、登记检测器映射。无内置检测器、脚本路径、动态 import 或用户参数执行。
- 执行语义：预检登记身份；每候选前后重查当前权限/版本，只读一次并校验 SHA-256；每步骤每候选运行一次，两个硬规则可复用同一检测事实；逐规则和逐素材输出 pass/fail/unknown。缺事实、验证器不一致、检测异常为 unknown；媒体变化、超限及授权/依赖 `ServiceError` 中止作业，不返回部分结果。整体 fail 优先于 unknown，但 unknown 永不算 pass。输出保留原 binding，不含有效去重数量。调用方在持久化发布时仍需再做原子 fencing 校验。
- 模拟与真实：9 项用例只用合成字节和合成检测器，属于 SIMULATED 框架测试。真实 DJ 当前仍只有旧单规则宽度桥接；本模块没有接入 DJ plan_flow、OCR/水印模型或真实清洗任务调度。
- 2026-10-22 16:56（Asia/Shanghai），Python 3.13.3：`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/cleaning -q` 退出码 0，9 项通过；`python3 tools/collab_gate.py check --role cleaning --base 2673bc02d65ab54c9e48acf6823d43b6f2e18dda` 退出码 0，SCOPE PASS / LOCAL GATE PASS，308 项通过。输出中的 LOCAL GATE FAIL 是现有负例测试产生，不是本轮门禁结果。`git diff --check` 退出码 0（新文件另行检查）。
- NOT_RUN：真实 DJ 多规则、真实素材/内网、授权服务、持久发布、恢复/并发及性能。后续需要 A 装配业务批准→作业、结果持久化与原子 fencing，登记真实算子及其评测证据；不得把当前结果计入业务 accepted_count。
