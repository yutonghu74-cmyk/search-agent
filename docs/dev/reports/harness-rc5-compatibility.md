# DSH 0.2.2-rc.5 基础兼容验证

## 目标与实际版本

用户指定直接采用 v0.2.2-rc.5。新双 Agent 接入配置默认指向 npm 官方 `@deepseek-ai/dsh@0.2.2-rc.5`，实际 CLI `--version` 输出为 `0.2.2-rc.5`。该环境独立安装，不覆盖既有 `.venv-dsh`，未重启或替换现场业务服务。

Python SDK 及其依赖 carrier 为 `0.2.2rc2`，SDK 使用明确 dsh_bin 启动上述 npm CLI；这是控制客户端与实际运行时的分离，不把 rc2 称为 rc5。Node 为已有 24.22.3。SDK 与 npm 依赖锁在 runtime-locks/dsh-0.2.2-rc.5/；临时安装目录不作为持久部署保证。

上游依据：[npm 官方 rc.5 元数据](https://registry.npmjs.org/@deepseek-ai%2Fdsh/0.2.2-rc.5)、[Python SDK 官方元数据](https://pypi.org/pypi/deepseek-harness-sdk/json)、[该 tag 的 SDK 指南](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.2-rc.5/python/development.md)。文档说明 SDK 可用 dsh_bin 显式选择 CLI；实际兼容性以本轮运行证据为准。

## 结果

旧0.2.1rc2与新0.2.2-rc.5分别运行三种场景：检索提案、清洗/打标处理提案、未批准来源拒绝。均使用真实SDK/native运行时与MCP子进程，模型请求仅到临时本机确定性HTTP接口，不访问真实外部模型或业务素材。

| 检查 | 结果 |
| --- | --- |
| 实际CLI版本与SDK/carrier元数据分别核验 | PASS |
| 从配置经统一装配函数创建两个角色路由 | PASS |
| 模型可见工具仅为上下文、当前角色提案、报告 | PASS |
| 无shell或其他角色工具进入模型请求 | PASS |
| 源上下文→实际MCP/Socket→共享业务校验→有效输出 | PASS |
| 未批准来源即使SDK最终正常结束也不能提交成功 | PASS |
| max_tokens=12000到达本机模型接口 | PASS |
| 模型请求无dsh_session_log/dsh_plugin_packages字段 | PASS |
| 每场景3次模型请求，包括最终文字回复 | PASS；这是观测，不是硬预算限制证明 |
| 本地函数/进程一致性、角色混用、取消、模型/产物不匹配 | PASS（共享定向组） |

定向命令使用已设置的 DSH_TEST_PYTHON、DSH_CANDIDATE_TEST_PYTHON、DSH_CANDIDATE_TEST_DSH_BIN、DSH_CANDIDATE_TEST_NODE、DSH_CANDIDATE_TEST_NPM_LOCK，所有HOME及测试账本隔离：

```sh
PYTHONPATH=src:tests python3 -W error::ResourceWarning -m unittest \
  tests/test_harness_native.py tests/test_harness_port.py tests/test_agent_contracts.py \
  tests/test_proposal_transport.py tests/test_drafts.py tests/test_retrieval_contracts.py \
  tests/test_multi_rule_contracts.py tests/test_recipe_bridge.py tests/retrieval/test_dsh_runtime.py -q
```

最终109项定向全部通过，无跳过。真实SDK联测因需要本机HTTP/Unix socket，经批准在沙箱外执行；未运行完整回归。本机已有旧SDK原生集成也在本轮实际执行，不再沿用此前“未配置环境跳过”的证据。

## 边界

这次验证证明运行协议、工具清单、版本选择和业务校验兼容；没有验证真实模型召回/标签准确率、模型服务费用限额、内部重试/压缩的统一预算拦截、跨实例租约及持久化恢复。T04f/T05/T06和真实试点仍未完成。execute工具和新模式正式任务确认门禁继续保持，不能把规划运行成功当成可上线完整双Agent业务。

当前`requirements-dsh.lock`仍保留旧路径的兼容基线；新接入使用独立rc.5运行锁与config/harness-releases.example.json。既有批准摘要未重算，真实账本未迁移，旧环境未删除，npm/pip没有全局安装。
