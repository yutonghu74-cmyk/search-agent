# Harness 运行端口（T04a–T04e 基础接入）

## 已实现与限制

新增 `harness/` 应用端口、显式注册、角色发布定义与路由、共享逻辑工具、规划回执以及本地回调/独立进程驱动。检索与清洗可使用不同驱动，不依赖原生 Team。SDK 只能通过适配层导入，旧 DshModelRuntime 和其批准摘要保持不变。

本片提供可调用的规划组件，不接管平台现场启动，也不解除 TaskService 的 AGENT_EXECUTION_UNAVAILABLE 门禁。T04的PlanningReceipts/运行事件仍为进程内状态；T05已新增可选同库持久规划组件，见 [持久化契约](harness-persistence.md)。读取、提案和报告可用，execute类工具继续强制拒绝，正式业务派发尚未接通。

legacy DSH 和实际 npm 0.2.2-rc.5 已通过真实 SDK/角色工具清单/MCP链路的基础兼容验证，详见 [报告](../docs/dev/reports/harness-rc5-compatibility.md)。模型出口硬预算与生产恢复仍需验证，其他真实 Harness 未接入，不能用测试驱动的成功代替。只有包版本 probe 成功不等于运行语义和生产隔离已经验收。

## 组件

| 模块 | 接口 |
| --- | --- |
| harness/contracts.py | HarnessAdapter Protocol、RoleDefinition、RuntimeRelease、HarnessInvocation、RuntimeHandle、RuntimeEvent、AdapterRegistry |
| harness/runner.py | HarnessRunner.start/poll/outputs/cancel/close，load_role_routes |
| harness/roles.py | 供应商无关、版本化检索/清洗提示与逻辑工具清单 |
| harness/tools.py | ToolCatalog、规划专用 ToolBroker、LocalTools/McpTools 传输包装 |
| harness/stage_tools.py | StageAuthority 回读、共享 T03 检索/处理/报告校验与解码 |
| adapters/harness/local.py | 包装可信 SDK 回调的异步线程生命周期；没有自行增加模型循环 |
| adapters/harness/process.py | 语言无关 harness-worker@1 标准输入输出协议、截止时间、进程清理 |
| adapters/harness/dsh_legacy.py / dsh_worker.py | 固定旧 SDK 的新端口驱动及 MCP 桥接；旧 SDK API 不泄漏到业务层 |

## 装配方式

由后端代码显式安装适配器和发布物，配置只选择发布 ID，不允许动态 import 任意模块。下面是旧发布的显式装配示意；新接入默认使用文末rc.5配置入口。authority/model_environment/model_binding必须连接后端可信快照，不能从模型工具参数取得：

```python
from dsh_retrieval.harness.contracts import AdapterRegistry, RuntimeRelease
from dsh_retrieval.harness.roles import DEFAULT_ROLES
from dsh_retrieval.harness.runner import HarnessRunner, load_role_routes
from dsh_retrieval.harness.stage_tools import stage_catalog
from dsh_retrieval.adapters.harness.dsh_legacy import LegacyDshAdapter

adapter = LegacyDshAdapter(
    python=isolated_python,
    home=isolated_short_home,
    model_environment=resolve_immutable_model_environment,
)
registry = AdapterRegistry()
registry.register('dsh-legacy', adapter)
release = RuntimeRelease('dsh-legacy-release-1', 'dsh-legacy', adapter.artifact_digest)
releases = {release.release_ref: release}
routes = load_role_routes({
    'retrieval': release.release_ref,
    'cleaning': release.release_ref,
}, releases)
catalog, guard = stage_catalog(read_current_stage_authority)
runner = HarnessRunner(registry, routes, DEFAULT_ROLES, catalog,
                       guard=guard, model_binding=resolve_immutable_model_binding)
```

这些默认能力只供规划组件验证。真实任务发布还必须要求/验证 hard_model_budget、工具隔离等部署能力；当前适配器不声明这些能力，配置强制要求时 probe 准入失败。不能将上例直接当作生产配置启用，也不能通过删减 required_capabilities 绕过上线验收。

新增 Harness 时：实现 Protocol（或包装可信本地回调、标准协议 worker），显式注册并声明经验证能力/产物摘要，发布 RuntimeRelease，修改对应角色路由，再运行共用测试。业务需求、T03 校验、工具处理函数、计数和 DJ 不需要修改。

## 输入、版本和输出

HarnessInvocation 引用已有 AgentStageTask，输入 context_json 必须是对象且摘要等于 stage.input_digest。角色/运行发布均不可变，分别有摘要；模型由不可变 model_profile_ref 解析，Runner 在启动前核对解析出的语义摘要与 stage.model_binding_digest。后端模型环境解析必须使用同一个不可变发布快照，不能读取会被用户设置即时覆盖的别名。

RuntimeRelease 记录适配器、发布物摘要和必需能力；注册表检查 probe 的协议、身份、能力和产物摘要。DSH 当前摘要包含桥接源码及 patch；实际 SDK/二进制锁定与安装证据仍属于真实兼容验证，不能据此声称校验了全部安装字节。

同 run_id/attempt_id 重复启动返回原句柄，参数变化冲突。运行尝试固定角色和引擎发布；路由更新只影响新尝试。取消返回 acknowledged，表示已封禁工具并请求停止，不把合作式线程取消解释为已经终止。close 关闭资源，已完成的有效规划输出仍可查询。

poll 使用句柄作用域内的递增 cursor，每页最多100条；调用方应以最后 cursor 继续读取。输出事件仅含规范类型与安全错误码，供应商文本/原始异常不出现在事件中。Runner.outputs 只对已 finished 的尝试返回经过工具校验的提案/报告及回执，且重新核验当前权限；这不是执行批准。

SDK 退出0、返回 completed 或普通文字不会单独生成成功。必须由本次调用共享业务工具产生有效提案/报告回执。角色不匹配、来源/步骤越权、旧版本、取消/超时和预算不足通过同一 T03/工具边界拒绝。

## 工具和幂等

角色仅暴露 get_stage_context、各自的 submit_retrieval_strategy/submit_processing_plan，以及 submit_stage_report。模型提交来源 binding_id 或已发布 step_id，后端解析实际物理绑定/DetectionStep，不接受模型指定身份、执行路径或输入新额度。

ToolDefinition 的 JSON schema 使用明确支持的子集：type、properties、required、additionalProperties、items/maxItems、字符串长度、数字范围和 enum。未支持关键字在注册时拒绝，不会无声忽略。深层业务字段继续由严格处理函数/T03 验证。

PlanningReceipts 是内存规划幂等：同阶段、同工具的提案/报告只允许一个不可变正文；不同原生 call ID 或新尝试的相同提交返回原回执，同键异体冲突。读取键包含参数指纹。缓存命中仍重验当前权限与取消状态。验证后的提案正文供协调器读取；它不会创建业务结果或直接修改状态。

跨重启幂等及有副作用动作必须等待 T05；不能复用本内存实现执行生产动作。未来 T05 必须替换为事务级预算/回执和不确定动作核对，不能仅把 execute 类型从拒绝改为允许。

## worker / MCP 协议

独立进程首行输入 `{protocol: harness-worker@1, invocation: ...}`，只含角色、授权输入、工具 schema、模型引用、任务类型、deadline、run/attempt。凭据由注册的后端环境解析提供，不写入运行信封。

worker 请求 `{protocol, type: tool, id, name, arguments}`；父进程返回 `{protocol, type: tool_result, id, value}` 或安全 error_code。worker 结束发送 `{protocol, type: done}` 并正常退出。done 仍须配合实际工具回执；缺失/未知/超长/截断帧失败。单帧上限500KB，并设置写出队列上限和非阻塞进程 I/O，取消与截止时间不依赖 worker 主动返回。

legacy DSH 的 MCP 子进程通过私有尝试目录的 Unix socket 连回 worker，再由上述协议回到父进程 ToolBroker。每次运行独立 DSH_HOME，固定 SDK/runtime 0.2.1rc2；不继承开发者全局配置，未配置或版本不匹配时失败，无自动安装或回退。当前进程清理依赖 POSIX 进程组；Windows 需增加等效进程管理适配，不能假设已支持。

## 定向证据

- tests/test_harness_port.py 覆盖本地/MCP共用校验、两个不同形态驱动、跨角色混用、模型/产物不匹配、固定路由、重复提交、取消/截止时间、缺少有效输出、错误脱敏、业务提案读取及 SDK 替身桥接。
- DSH 替身测试创建临时 metadata 和假 SDK，仅用于验证真实子进程/Socket/MCP 协议接线，不是依赖安装，也不是原生 DSH 兼容证据。
- 旧契约/草稿/配方与旧 DSH 定向回归仍须保持；真实 SDK 环境未配置时如实报告跳过，不缩小发现范围。


## rc.5 默认发布

新接入使用 adapters.harness.composition.build_planning_runner 与 config/harness-releases.example.json，两个角色均指向rc.5。DshSdkRelease分别保存PythonSDK/carrier版本、API形态、运行来源、实际native_version与tag；npm模式必须指定dsh_bin、Node和锁文件，probe同时核对npm包和实际CLI版本，无wheel回退。旧0.2.1rc2仅为显式兼容选项。

安装锁及复现入口在 runtime-locks/dsh-0.2.2-rc.5/。Python控制SDK0.2.2rc2不等于实际Harness版本；运行时通过明确CLI选择实际0.2.2-rc.5。新装配是规划组件，不自动迁移现有平台进程或解除正式任务确认门禁。


## T05 可选持久装配

已新增 HarnessJournal/DurableHarnessRunner/DurableToolBroker，build_planning_runner同时提供journal和后端actor时启用；它们与原内存组件明确区分，无自动内存回退。当前持久适配器与业务SQLite同库，以事务重验任务、租约和权限。rc.5持久模式通过模型网关逐请求准入，并在有效提案回执后结束规划；普通内存模式不宣称有该预算保证。完整语义与验收见 [持久化契约](harness-persistence.md)。正式执行与新模式公共确认门禁仍保持。
