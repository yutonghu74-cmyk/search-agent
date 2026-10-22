# 授权媒体物化与候选预览

- A / codex/integration / var/worktrees/integration；起点 4740d998e54961752dd062b796c9e5f8f0f538c9。A 维护 materialization_models.py、ports.py、本任务卡、模型测试和 integration 交接。
- retrieval / codex/retrieval-adapters / var/worktrees/retrieval：同步 A 契约提交后，在交接登记完整新基线，范围仅 adapters/retrieval/、tests/retrieval/、retrieval 交接。
- 依据 v1.9 6.6、10.5、13.3、14.6；A12/A20/A28/A36/A44/A45/A50 的本地子集。只读取已提交且当前有权的检索页；映射完整、顺序与原命中一致；媒体身份绑定 namespace/source_asset/media_unit/version；同身份合并命中，不做内容去重。
- 每个素材必须有可核验 sha256 摘要（缺失返回 MEDIA_VERSION_UNVERIFIED）；只由 MediaAccessGateway 打开可信引用。物化器独立执行单素材和整页读取大小上限，流式摘要；不信任网关仅宣称限制。没有图片解码或业务清洗通过承诺。
- 临时输出由服务端独立根目录管理，不接受用户路径；context 退出自动清理。空页无需媒体读取。失败、映射变化、任务变化或撤权不得返回可用预览。读取结束重查媒体映射、权限和任务资格，但不声称跨系统 ACL 原子事务。
- MaterializedPage 是内部临时候选预览，view 不含路径或媒体引用；不是持久 manifest、不创建正式业务标识或清洗 operation、不修改有效数量。后续候选确认/发布须独立账本和生命周期契约，不能将本临时目录当作持久素材仓库。
- 允许本地编辑/测试、验证后提交及审查集成；不安装依赖、不访问内网、不运行真实 DJ/DSH。工作区环境/运行目录独立。
