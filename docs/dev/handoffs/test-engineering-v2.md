# test-engineering-v2 工作包交接

- 工作包 / 主责 / 分支 / 独立 worktree：test-engineering-v2 / integration / `codex/test-engineering` / `var/worktrees/test-engineering`。
- 任务卡 / 可信基线 SHA / 当前代码状态：`docs/dev/tasks/test-engineering-v2.json` / `23b00785e144d2fa7b85750eb94669bdaee37da1` / 实现提交 `bab7af3` 已集成 master。
- 目标 / 规格验收编号 / 修改范围：统一无手工 PYTHONPATH 的测试入口并冻结真实依赖测试路线；对应 v1.9 第 16、17 节测试与试点边界；范围以任务卡六个文件为准。
- 状态：ACCEPTED。
- 关键实现 / 接口兼容性 / 影响其他包：新增入口，不改变 unittest 用例和发现命名；旧命令继续可用；无公共业务接口变化。
- 风险与测试策略：full；测试执行工具若漏发现或吞失败会影响全仓证据，因此任务卡要求完整回归。
- 实际命令 / 测试数量 / 退出码 / 结果：`python3 tools/run_tests.py tests.test_test_runner -v`，8 项 PASS，退出码 0；工作包内 `python3 tools/run_tests.py --all`，581 项 PASS，退出码 0；`git diff --check`，退出码 0；`python3 tools/collab_gate.py package --base 23b00785e144d2fa7b85750eb94669bdaee37da1 --task docs/dev/tasks/test-engineering-v2.json`，581 项 PASS，退出码 0；集成最新检索协调器提交后的 master 再运行 `python3 tools/run_tests.py --all`，596 项 PASS，退出码 0。
- 范围检查结果；定向不等于全量，模拟不等于真实：`PACKAGE SCOPE PASS`、`LOCAL GATE PASS`；入口定向黑盒 8 项，工作包门禁执行完整本地回归 581 项。两者均不代表真实依赖验收。
- NOT_RUN / 风险 / 下一步：真实连接器、两库端到端、撤权竞争、生产持久化崩溃恢复、性能与成本基线均 NOT_RUN，等待真实依赖和隔离环境。

## 开发明细

- 2026-10-24：按新工作包约束从可信基线启动独立分支；避开 R06 与其他窗口未提交修改。
- 2026-10-24：统一入口及真实依赖测试路线完成；初轮工作包范围检查和 581 项完整回归通过。更新最终交接后再次运行同一门禁，以最终代码状态为准。
- 2026-10-24：统一入口首次全量自测暴露两项路径问题：根 `tests/` 非包时显式 top-level discovery 失败，且仅修改 `sys.path` 不能支持测试启动的子进程。已恢复现有发现语义并把必需路径写入子进程环境；随后入口定向 8 项及入口全量 581 项均通过，并将跨进程恢复用例纳入入口黑盒回归。
- 2026-10-24：主干并发增加检索协调器与 SQLite 恢复测试，和本包文件无重叠；实现安全集成为 `bab7af3`，合并后的最新 master 由统一入口执行 596 项完整回归通过，工作区干净。
