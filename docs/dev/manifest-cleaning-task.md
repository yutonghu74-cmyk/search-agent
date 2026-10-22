# 已发布候选清单接入清洗

- A / codex/integration / var/worktrees/integration；基线 59ce9e8ead931bbe68959998238c0a788de8f8e2。范围：manifest_cleaning.py、cleaning_service.py、adapters/dj_cleaning.py、tools/dj_cleaning_bridge.py、tests/test_manifest_cleaning.py、本任务卡、integration 交接。均为 A 所有文件，不接管 core/retrieval 目录。
- 依据 v1.9 13.3/13.5/14.6 和 A12/A20/A26/A28/A36/A45 的本地子集。不新增依赖、不执行真实 DJ/DSH、不访问内网、不推送。
- 提供绑定 actor/task 的清单 catalog 和执行器装饰器。清洗提案只存稳定身份/版本/摘要，source_path 为空内部占位；提交阶段经可信授权网关下载，独立限制单项/总量，验 SHA-256，提交前重查清单。仅支持明确声明提交时冻结输入且路径不参与幂等的后端。
- 提案准备不下载或派发；已发布候选确认不替代清洗 action=confirm。清洗开始仍使用既有 CAS/generation 与幂等账本。只支持已存在 image-width@1 固定规则，不能称为需求全规则实现。
- DJ bridge 显式 ephemeral_inputs 模式忽略 source_path 的重试差异，但身份、摘要、配方、binding 均仍进入指纹；默认旧模式保持原完整指纹。提交同步冻结源字节后可释放下载目录；poll/cancel 不依赖原临时路径。
- 权限/清单在提交前后、轮询及结果读取再次核对；撤权不阻止必要的取消请求。跨系统撤权仍非原子；后端冻结副本按后端保留策略管理，不将下载临时目录清理称为副本销毁。
- 验收：发布→清洗提案→单独确认→模拟逐条回传；提案无失效路径；重复确认无重复提交；跨进程恢复；变更字节、撤权、超限、旧代次拒绝；提交异常清理；空清单/不支持配方拒绝；默认/新指纹兼容与路径变化重试测试。
- 本轮不提供 HTTP/UI、自动补数、生产调度或真实 DJ 验收。A 无专用角色检查器，按要求运行既有角色检查并记录分支不匹配，不修改门禁绕过。
