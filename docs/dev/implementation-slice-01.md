# v1.9 第一轮落地：草稿确认 + DJ 图片探针

## 范围与状态

2026-10-21，按用户“复用清洗能力，新建业务系统”启动。角色为 A 总控集成，在当前主工作区基于 cf17b1c 实施一个单窗口切片；未启动 B/C/D、子代理或额外 worktree，未推送远端。本轮文件范围为业务模型/服务/内存适配/HTTP/公共契约、对应测试和本地验证工具。此记录不是对后续窗口越界修改的授权。

已实现：

- POST `/v1/retrieval/projects/{project_id}/drafts` 保存草稿，不建立 task/batch。
- GET/POST `/v1/retrieval/projects/{project_id}/drafts/{draft_id}` 读取/修订；修订需 expected_draft_version，项目/子维度不可变。
- POST `/v1/retrieval/tasks` 只接受明确确认，校验发起人、当前项目授权、版本及 SHA-256 摘要；内存锁下原子建立 task/batch、冻结方案及幂等记录。
- 同草稿使用不同确认键仍仅创建一次；已确认草稿不可覆盖；返回 PLANNING、confirmed_revision=1、pending_draft=null。
- 新创建使用 query-spec@2；统一去重/补数字段，旧字段混写 SCHEMA_CONFLICT。当前只启用图片目标/文字需求、exact-content@1、无历史基准、input_only/manual。
- 五节点仅启用 manual；省略时显示并冻结系统 manual 默认。项目级模式配置、auto 模式尚未启用。
- 默认无项目授权；开发启动参数 `--demo-project` 显式授予 tenant-a/user-a → demo/default。生产身份仍须由 W3 提供。

确认后的不可变内容目前包含归一化 QuerySpec、子维度和门禁模式；项目、创建人、时间、摘要保存在正式任务。完整 revision/branch/run 实体、检索计划/输入快照、确认事件及 outbox 尚未实现，不能将此切片称作完整 A97 或生产事务实现。API 不会自动派发 DJ。

## 验证

- `python3 tools/collab_gate.py baseline`：38 项单元测试通过，覆盖既有状态机与幂等、草稿无正式记录、不同键并发确认一次、并发修订 CAS、陈旧摘要、非发起人、撤权后重放/读取、旧接口拒绝、非法字段及手动默认策略。
- `PYTHONPATH=src python3 tools/api_smoke.py`：真实 loopback HTTP 七项检查通过；需要允许本机绑定端口，结束自动关闭服务器。
- `var/dj-runtime/bin/python tools/validate_api_contracts.py`：实际 HTTP 请求/响应与 OpenAPI 3.1、QuerySpec v2 Schema 匹配。
- DJ 真实图片探针通过：三张本地生成图片，image_shape_filter(min_width=32) + image_audit；逐图覆盖 3，pass 2、fail 1；准备重放、批准门禁、运行重放及冲突检测通过。没有替换为 fake 算子，也未禁用 runtime preflight。

对应规格 A97/A104/A106/A107 的部分本地变体，不代表这些验收项整体完成。失权测试为请求入口实时复核；跨系统撤权与提交竞争仍需生产授权/事务机制验证。

## DJ 隔离运行环境

本轮安装到 `var/dj-runtime`（Python 3.10.13，约 373 MB），未修改旧 `.venv` 或 `uv.lock`。恢复的是锁文件中的基础依赖，不含全部 GPU/模型 extras；84 项运行依赖闭包预检通过，不等于完成依赖安全审计。

```bash
UV_PROJECT_ENVIRONMENT="$PWD/var/dj-runtime" \
UV_CACHE_DIR="$PWD/var/uv-cache" \
uv sync --project "$PWD/data-juicer-codex-dj-plan-explorer" \
  --frozen --no-default-groups --no-install-project \
  --python "$PWD/data-juicer-codex-dj-plan-explorer/.venv/bin/python" \
  --no-python-downloads

PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$PWD/data-juicer-codex-dj-plan-explorer" \
var/dj-runtime/bin/python tools/dj_pipeline_smoke.py
```

新机器需先提供 Python 3.10 解释器，并替换上述 `--python` 路径；本命令不安装源码项目本身，显式 PYTHONPATH 指向现有源码。依赖仍需整理为可分发工件，不能宣称内网部署已完成。

已定位并处理的本机运行限制（未修改 DJ 源码）：

1. 沙箱中 psutil.cpu_count() 返回 None；获准的本机环境下重跑。
2. macOS multiprocessing 的 Unix socket 路径过长；探针使用短的 `/tmp/d...` workspace。不能把生产工作区路径无条件套用本机临时目录。

成功原始报告：`/tmp/d50hpqt1c/smoke-report.json`（临时目录可能被清理）。输入输出均为合成图片，无公司数据。探针单用户批准是诊断流程，不能直接变成 Agent 自行批准业务方案的工具。

关键工件 SHA-256（仅这些文件，不是全源码哈希）：

| 工件 | SHA-256 |
| --- | --- |
| uv.lock | b0765d52bb602a6a262456f720b7e04c5f08addc6c38126083b106ac99eb794b |
| plan_flow/service.py | e3a9b7aa8b15c01a883a3e98f8da33b7dccff8568a7f8dda4854499b8073eda1 |
| plan_flow/image_audit.py | 842bd67eb696e8bdfe7d6d374d218ca04d861777ba306fa9e9feb3e1eac65a63 |

## 接下来实现，而非等待全部契约完成

1. 把已验证清洗调用封装为独立执行适配器：稳定候选 ID/素材版本、operation/attempt、业务与 DJ ID 映射、输入/配方摘要。
2. 补齐正式 revision/branch/run、确认事件/outbox，加入 generation/plan fence 后才能把 Worker 结果接回业务状态。
3. 用模拟检索接通逐项判定→去重计数→受限补数，再落地持久化适配并验证重启恢复。
4. ClickHouse、Milvus、W3 接口真实联测；未联测保持 NOT_CONNECTED/NOT_RUN。

不默认安装 PostgreSQL，不把 DJ 文件或 ClickHouse 当任务账本。不把 output_count（文件数）当有效图片数，不将 DJ run 成功等同于业务任务 COMPLETED。
