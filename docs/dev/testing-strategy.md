# 测试工程与真实依赖验收策略

## 统一入口

仓库测试统一从根目录执行 `python3 tools/run_tests.py`，调用方不再手工拼接
`PYTHONPATH=tests` 或 `PYTHONPATH=src:tests`。入口只封装路径和 unittest 目标解析，
不改变 `test*.py` 的全量发现规则，也不把空目标、导入错误或失败测试转成成功。

```bash
# 完整发现
python3 tools/run_tests.py
python3 tools/run_tests.py --all -v

# 模块、类、方法，或 tests 下文件
python3 tools/run_tests.py tests.test_query_routing -v
python3 tools/run_tests.py test_query_routing.QueryRoutingTest
python3 tools/run_tests.py core.test_counting.CountingTest
python3 tools/run_tests.py tests/test_query_routing.py
```

协作交付仍以任务卡规定的 `tools/collab_gate.py package`（旧任务为角色 `check`）
为准；统一入口不能替代范围检查或把定向测试表述为完整回归。

## 真实依赖测试分层

以下用例要在依赖、测试租户和可清理数据准备完成后增加。当前环境不满足进入条件时，
状态必须记录为 `NOT_RUN` 或 `BLOCKED`，不得用 mock、空测试或 skip 冒充通过。
真实用例使用显式命令和隔离配置，默认不进入无外部服务的本地全量测试。

| 层级 | 建议位置 | 进入条件 | 最低验证与证据 |
| --- | --- | --- | --- |
| 连接器契约 | `tests/connectors/` | 固定产品/版本、只读测试端点、测试身份和最小样本 | ClickHouse 元数据与主键映射、Milvus 维度/度量/分页、媒体读取与授权的成功/拒绝/故障；记录服务版本、配置摘要和脱敏日志 |
| 两库端到端 | `tests/e2e/` | 两个隔离库、不同编码器或索引绑定、可重复种子数据 | 分支路由、编码、跨库合并、去重、有效计数与补数；保存任务/revision/generation、索引快照和权威结果摘要 |
| 撤权竞争 | `tests/e2e/` | 可控制授权变化时点，连接器支持阻塞或故障注入 | 请求前、查询中、回传前撤权均失败关闭；旧运行不能覆盖新结果；保留时序、审计事件和最终账本状态 |
| 崩溃恢复 | `tests/recovery/` | 持久化业务账本、可终止 worker、稳定幂等键 | 提交前后、回传中和最终发布前强制退出；重启后不丢任务、不重复副作用，旧 generation 被栅栏；保留恢复前后快照与外部 operation ID |
| 性能与成本基线 | `tests/performance/` | 冻结数据集、并发模型、机器/服务规格及计价版本 | p50/p95/p99 延迟、吞吐、失败率、查询/模型/媒体调用量和单位有效素材成本；保存原始指标、预算阈值和回归比较 |

## 执行与记录规则

- 每次真实运行固定代码 SHA、依赖版本、模型/预处理版本、库与索引快照、数据集摘要、
  测试时间窗和完整命令；证据中删除凭据、内网地址和真实业务内容。
- 契约测试先于端到端；撤权竞争与崩溃恢复必须验证最终账本和外部副作用，不能只看 HTTP 返回。
- 性能和成本先建立可复现基线，再设阻断阈值；服务配额、缓存冷热状态和重试次数必须入记录。
- 真实依赖不可用、权限不足或数据不稳定时单独报告原因和影响，不降低成本地模拟通过。
- 凭据、连接串与真实样本不提交仓库；只提交脱敏配置模板、测试代码和可审计结果摘要。
