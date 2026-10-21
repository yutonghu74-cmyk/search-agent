# A 总控集成交接

- 角色：A；master；主工作区 /Users/hw-edit/Documents/ChatGPT/search-agent；未启动并行 worktree。
- 基线：cf17b1c5fd5e4218b9801bb82535c0b5555c6dfd；当前改动未提交。
- 状态：READY_FOR_REVIEW（本地切片），非全规格验收通过。
- 范围、契约变更与测试证据：见 implementation-slice-01.md、implementation-slice-02.md。CleaningExecutor 改为 operation 对象的 submit/poll/cancel，增加 CleaningRepository/CleaningCatalog；后续角色须同步新契约，不能沿用旧三参数 submit。
- 48 项单测通过；真实合成图片 DJ 服务联测通过；角色门禁失败（master 不符合 core 分支要求），未绕过检查。
- 内网、持久化重启、生产权限竞争和性能测试 NOT_RUN；未提交、推送、合并或改旧 DJ 源码。

## 后续集成（以上为历史记录）

- 用户已统一授权通过验证后的本地提交/合并，仍不推送、不部署、不接真实数据。
- core 在独立工作区通过角色门禁及 60 项测试，提交 aa179ea17527c3e4a8d24619fe65cb4f346de2ef，已快进集成。
- 当前角色 A / codex/integration / var/worktrees/integration，基于 aa179ea；任务卡 docs/dev/intake-task.md。
- CandidateIntake 接入服务端固定合成输入：预览 → 明确确认 → 不可变任务绑定清单，幂等重放、作用域/输入变更校验；实现 CleaningCatalog，清洗仍需单独明确确认。不实现自然语言检索、分页或空召回处理；当前固定清单要求 1–100 条，空清单按 INPUT_LIMIT 拒绝。
- 共同契约增加 CleaningCatalog.require_scope(task, manifest_ref)，清洗 prepare/start 和 core 预览读取前调用。既有 LocalCleaningCatalog 是项目级测试夹具；CandidateIntake 才增加 tenant/owner/project/task/revision/generation/confirmed-proposal 绑定。下游新适配器须实现该方法，不可静默跳过。
- A 接手 core 中唯一变动是上述契约调用，不改计数算法；无其他窗口在写代码。使用完整回归验证共享变更，不改责任表或检查器冒充 core 角色范围通过。
- 67 项单测通过，新增 7 项 intake 测试；git diff --check 通过。core 角色门禁在集成前 PASS；A 无专属角色检查器，不宣称共享变更通过 core 范围门禁。
- tools/cleaning_smoke.py 已串联固定候选确认 → 真实 DJ → 去重预览，4 张合成 PNG 含一对相同内容。主工作区集成 61f9b07 后真实运行 PASS：pass=3/fail=1/unknown=0，unique_pass_count=2、duplicate_count=1，accepted_count=null，任务 RUNNING。重复 start/poll 不重复发布。报告 var/cleaning-smoke.json，合成运行目录 /tmp/dhecdltxf（临时目录可能被清理）。独立集成工作区未共享 DJ 可变运行环境。
- 合并后在主工作区再次执行全部 67 项测试，通过；工作区干净。未推送或接真实数据。下一步优先提供可操作的演示入口；正式检索/结果发布仍须分别冻结契约，不能把固定候选来源改名为真实语义检索。
- 仍为单进程内存清单；跨系统授权/发布竞争不是生产事务保证。真实检索、业务全规则判定、持久化、正式结果发布与补数 NOT_RUN。

## 交互演示入口

- A / codex/integration / var/worktrees/integration，基线 bd395f2；任务卡 docs/dev/demo-task.md。
- 新入口 `python3 -m dsh_retrieval.demo --executor simulated|real`，依次人工确认需求、候选、清洗；无自动批准开关。执行器必须显式选择；缺少真实环境不自动降级。
- 新增 6 项测试，完整 73 项通过：三确认点停止不派发、完整模拟预览、超时取消、Ctrl-C 取消、提交传输异常的已预约 operation 栅栏、失败全 unknown。终端模拟模式实际运行通过，报告 /tmp/dkx1rm7us/demo-report.json。
- core 原交付再次运行角色检查 PASS（60 项），A 演示增量使用全量 73 项回归；A 无专属角色检查器，不伪造角色范围通过。
- 演示用短独立临时目录，报告标注 synthetic_fixed_input 与执行模式，保留证据但不提供进程重启恢复。未推送、部署或读取真实数据。
