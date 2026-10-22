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
- 合并 87ee197 后，主工作区全量 73 项回归通过；真实终端交互 `--executor real` 经三个确认点运行成功，报告 /tmp/d2zhzqvqk/demo-report.json：status=preview_ready、清洗 3 pass/1 fail、唯一通过 2、重复 1、预览缺口 1、accepted_count=null、任务 RUNNING。这是开发者使用合成数据的验收，不代表用户已验收业务效果。

## 本地持久化与恢复（更新上述内存演示边界）

- A 分派基线 551eae4052fbc9e1dacf7c086ecc2972c0dc7d39，core 按 docs/dev/storage-task.md 在独立工作区完成 SQLite 仓库，角色门禁及 81 项测试 PASS，提交 78e457e 后集成。
- A / codex/integration / var/worktrees/integration 在 78e457e 上装配演示恢复；公共仓库协议不变。交互演示改用 workspace/ledger.sqlite3，清洗批准后以原子替换保存 session.json；HTTP 入口仍为内存，未宣称全部入口持久化。
- `--resume <原目录>` 校验原执行模式、任务/代次和源文件摘要，使用原任务/operation/批准摘要。已完成操作不重新提交；已批准而未完成的操作支持幂等继续；暂停/取消拒绝自动恢复。候选批准前状态、已清理临时媒体、旧演示目录不支持该恢复入口。
- A 新增 5 项测试，完整 86 项通过：完成会话由新进程恢复、模拟提交中断后的已预约 operation 重用、内容变化拒绝、模式不匹配拒绝、取消后拒绝。提交中断通过注入 SystemExit 模拟，非真实断电测试。
- SQLite 为本地开发单快照实现，16 MB 上限；schema format=1，版本不匹配或坏记录失败关闭，不自动清库。不是内网生产选型、SSE/outbox 或后台恢复调度；跨系统授权事务与断电故障注入尚未验证。无新增依赖、无 PostgreSQL 安装。
- 主工作区集成 3ae6249 后，86 项回归 PASS；真实 DJ 新会话 /tmp/dm6avsi8a 运行成功，再由独立 CLI 进程执行 `--executor real --resume /tmp/dm6avsi8a`，恢复 PASS。operation_id 始终 clean_e7dfd2bd0ddd43e5a197bed94d2e1aab，task_version=3、unique_pass_count=2 不变，原 DJ run_r001 未重新提交。证据 /tmp/dm6avsi8a/demo-report.json（合成临时数据）。这验证已完成作业跨进程恢复；真实运行中的强杀/断电恢复尚未实测。

## 2026-10-22 检索计划公共契约（未集成）

- 角色 A / codex/integration / /Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/integration；基线 b2cdedd7b966ad7e4756c127525f81dafd9400fc；任务卡 docs/dev/retrieval-contract-task.md。
- 状态 READY_FOR_REVIEW（契约级）；当前代码未提交，未发布新基线，主分支不变。上一轮 retrieval 模拟适配仍在其独立 worktree，本轮未复制或修改其代码，不把两边测试数量相加当集成验收。
- 新增 RetrievalScope / RetrievalPlan 与无副作用校验器；新增 RetrievalPlanRepository 端口，定义计划确认的 CAS、同一提案多键幂等、任务版本递增、page 预约与事务提交。contracts/retrieval-plan.md 明确请求、错误、兼容和未实现范围。
- 修改文件：src/dsh_retrieval/retrieval_contracts.py、src/dsh_retrieval/ports.py、tests/test_retrieval_contracts.py、contracts/retrieval-plan.md、docs/dev/retrieval-contract-task.md、本交接文件。
- 校验覆盖：创建人/租户、project/task/revision/generation/需求摘要/task_version；未确认及旧 epoch；暂停/取消/终态/归档/FINALIZING；请求篡改；编码器/维度；空页、深度上限、游标循环/前驱/快照变化、live 不声称耗尽；错误绑定/超页上限/非有限分数/损坏命中。
- 本轮不是确认服务或生产账本：校验函数不会批准/派发/写候选/更新计数。现有 CandidateIntake 不变，空检索页与清洗链路尚未装配；没有新的 HTTP 路由。
- 本地验证：2026-10-22 10:02（Asia/Shanghai），Python 3.13.3，`PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q` 首轮 101 项通过、退出码 0；追加边界测试后结果见后续记录。
- 最终回归：2026-10-22 10:03（Asia/Shanghai），同一命令退出码 0，103 项通过（基线 86 + 本轮新增 17）；新增 FINALIZING 禁止提交及损坏命中显式错误用例。与 retrieval 分支的 104 项属于不同工作区，不是测试减少，也未完成双分支集成验证。
- 角色门禁：`python3 tools/collab_gate.py check --role retrieval --base b2cdedd7b966ad7e4756c127525f81dafd9400fc` 退出码 1，原因 codex/integration 不符合 retrieval 分支。A 没有对应角色检查器，不改责任表/检查器，不使用 baseline 模式替代；未宣称门禁通过。git diff --check 通过；未跟踪新增文件另检查空白。
- NOT_RUN：原子批准和幂等存储、真实权限撤销竞争、跨进程持久化、实际 ClickHouse/Milvus、真实 DJ、端到端检索→清洗；A20/A49 的实际授权/依赖故障不由纯校验器保证。
- 下一步：用户确认本地提交/集成权限后，先发布 A 契约基线；再派发 core 任务账本原子确认实现，retrieval 同步消费已批准记录，A 做端到端装配。现有检索模拟批准夹具不得替代账本，也不能将本契约记录标为 P01 完成。

## 本地集成授权与契约发布（2026-10-22）

- 用户已确认本地提交与审查后合并；不推送、不部署、不访问内网。上述“未获提交授权”为历史状态。
- A 重新审查公共契约、调用边界和测试，并在独立 integration 工作区重跑完整 103 项测试，退出码 0。只读审查 retrieval 适配切片，其原基线角色检查通过、104 项测试通过；下一步同步本契约基线后再验证组合。
- 本提交发布契约级开发基线。纯校验器、协议定义可用于后续开发；原子确认/任务事务/真实连接器尚未实现。A 无专属角色检查器的限制保持，不把完整回归冒充角色门禁。

## 检索模拟适配合入主分支（2026-10-22 10:14，Asia/Shanghai）

- 公共契约提交 4d666cd57907cc0e4abfa4e914e575469208b5b4 已快进合入 master，主工作区随后 103 项回归 PASS。
- retrieval 同步该契约基线，新增两项实际适配输出与公共校验器的组合用例。`python3 tools/collab_gate.py check --role retrieval --base 4d666cd57907cc0e4abfa4e914e575469208b5b4`：退出码 0，角色范围及 123 项测试 PASS。
- A 审查模拟适配代码及其验证边界后，提交 734efc3 已快进合入 master。合并后在主工作区执行 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`，退出码 0，123 项通过（86 基线 + 17 契约 + 18 适配 + 2 组合）。git 暂存 diff 空白检查通过，未改检查器或测试发现规则。
- 验收状态：ACCEPTED，仅公共契约/模拟适配开发切片。正式业务确认与检索计划持久化、候选发布/清洗装配、真实 ClickHouse/Milvus 全部未验收；P01 仍未完成。上文“待本地提交/基线未发布”记录已由此更新。
- 证据路径：tests/test_retrieval_contracts.py、tests/retrieval/test_simulated.py、docs/dev/handoffs/retrieval.md；本节记录实际执行结果，非真实 DJ 或内网联测。未推送、部署、安装依赖或接内网。
- 下一步：core 在本次集成基线上实现与任务共事务的检索计划账本，验收幂等确认、页预约/回传栅栏和重启恢复；A 再装配到业务流程。需要新任务卡及独立 core worktree，不能把模拟夹具当作已批准业务计划。

## 检索计划本地账本集成（2026-10-22 10:28，Asia/Shanghai）

- core 任务基线 7e9e33832352b22e2d68fcb82bdb86cca44973c1；任务卡/交接在 docs/dev/handoffs/core.md。实现提交 4db8db0，经 A 审查代码、角色范围及测试后快进合入 master。
- core 角色门禁通过；新增 21 项存储测试。合入后在主工作区执行 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`：退出码 0，144 项通过。无真实 DJ/内网执行。
- ACCEPTED 仅指本地存储切片：批准计划与任务版本同事务、同页预约/回传幂等、游标及版本栅栏、页与待发事件原子保存；新连接/新进程恢复原批准和预约。另以模拟网关验证两页预约→搜索→提交；没有自动候选发布或派发清洗。
- 旧任务/清洗 payload 不变；同库新增 retrieval_ledger 表，未触碰用户已有运行库。旧二进制不能直接读取新增表的账本。生产数据库选型未变，PostgreSQL 非必装。
- A 本轮只更新本记录及契约说明，无共享运行时代码变更；角色门禁证据采用本次 core 的实际通过结果，不宣称存在 A 专属检查器。
- 尚缺公共的待执行作业/已提交页读取接口、候选发布、HTTP/清洗装配和事件 relay；当前支持已知幂等键恢复，不宣称后台自动接管。下一步先冻结这些接口后分派实现。

## 授权作业读取契约（2026-10-22）

- A / codex/integration / var/worktrees/integration；基线 ecc58429096b94136627eae6b4fcab2e846926e5；任务卡 docs/dev/retrieval-read-task.md。响应 core 已登记的读取接口缺口，新增 RetrievalOperation、get_operation、list_pending，不改变已有写入接口或存储格式。
- 本提交发布契约基线，core 同步后实施。只读当前批准计划，区分 reserved 与已提交空页；重验身份/当前权限/版本；不派发、不加 lease，不作为媒体读取权限。
- 完整回归 146 项通过（原 144 + 2 返回模型用例），命令 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`，退出码 0。A 按要求运行 core 角色检查，因 codex/integration 非 core 分支退出码 1；没有 A 专属检查器，不改检查器绕过。core 实现需独立通过角色门禁。

## 授权读取本地集成（2026-10-22 10:50，Asia/Shanghai）

- 契约提交 8131b443b3222285d26a313f65574d4e52b3e913；core 在此基线上交付 0ad0609，角色检查通过。A 审查读取鉴权顺序、版本拒绝和无写入路径后快进合入主分支。
- 主分支合入契约后 146 项测试通过；合入实现后执行 `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q`，退出码 0，158 项通过（144 原基线 + 2 模型 + 12 读取）。独立测试发现检查确认 ID 唯一，不使用首次含重复发现的 179 次作为数量证据。
- ACCEPTED（本地授权读取切片）：无旧幂等键也可按任务列举待执行预约；可读取当前计划的已提交页，空页明确为 committed；读不改任务/事件、不自动执行；跨任务、旧 scope/plan、暂停/取消/撤权和故障失败关闭。
- 证据：tests/core/test_retrieval_reads.py、tests/test_retrieval_contracts.py、docs/dev/handoffs/core.md。数据库 schema 不变，未修改用户运行库、未访问内网或真实 DJ。
- 下一步候选发布/媒体物化仍缺契约与装配。作业读取不是历史结果浏览/媒体下载授权，也不是自动恢复调度或租约。当前没有承诺多个恢复进程不会重复发起外部请求。
