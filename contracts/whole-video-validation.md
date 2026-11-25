# 整段视频候选、验证与有效计数

状态：本地后端实现与合成数据回归；公共执行门禁仍关闭，不代表内网接口或真实模型已接通。

## 范围

只支持 query-spec@3 / text→video / distinct_video。条件只有 visual，关系为同一原视频内容上的 all/any，不承诺精确同时、连续动作、指定时间区间或全程满足。不支持片段、字幕/ASR/OCR、图片找视频或独立文字交付。

## 原视频稳定身份

- 复用公共 MediaRecord、MediaResolution、CandidateManifest 与 CleaningCandidateRef，不建立第二套视频候选模型。
- 可信连接器核验源单元确为 whole_video 后，调用 `canonical_whole_video_record(media, source_unit_type='whole_video')`。规范 unit ID 由 source_namespace、source_asset_id、asset_version 派生，独立于库、向量主键、播放 URL 和索引入口；原来源及授权引用保留。
- 传 frame/clip 等单元类型明确失败。调用函数不是自动证明单元粒度；连接器仍须从实际协议/登记中验证，不能给帧映射硬填 whole_video。
- 必须提供不可变源版本、SHA-256 内容摘要和授权引用。同一原视频在同一批次或累积结果链出现冲突版本/摘要时拒绝合并，不能按两个原视频计数。
- LocalCandidateMaterializer 的服务端 `media_type='video'` 模式只接受规范 whole-video 记录。多向量命中同一原视频只物化一次，并保留全部命中证据；所有读取、摘要、映射快照及当前任务复核继续执行。
- 默认 image 模式、公共 dataclass 字段和既有 SQLite 编解码格式不变，不修改图片历史身份或摘要。

## 验证配方与执行

现有 `compile_confirmed_recipe`、`require_confirmed_rules` 与 ConfirmedCleaningService 增加视频路径，复用 ReleasedDetectorRegistry、GenericCleaningExecutor 和原 SQLite 批准/检查点/结果账本。

视频配方使用一个聚合谓词 `video.content-match@1`。其 source_review_digest 绑定 task/revision、不可变需求摘要、distinct_video 和完整规范化条件（包括 all/any）；它不是将多个条件混成一个无约束提示词。

WholeVideoConditionDetector 是服务端注入适配器，接口为：

```python
rows, evidence_ref = evaluate(content_bytes, candidate_ref, cleaning_binding, conditions)
```

- rows 是按确认条件顺序返回的 `tuple[CleaningRuleVerdict, ...]`，每行绑定确切条件 ID，判定为 pass/fail/unknown。不能缺条件、重排、使用 not_applicable，或返回裸相似分数。
- evidence_ref 指向部署方实际保存的完整观察证据；每条已知判定也须有证据引用。不生成虚构引用替代真实观察，不传任意公开 URL 或文件路径作为证据标识。
- evaluator 负责真实解码、模型观察、覆盖策略及证据持久化。仅有整段向量分数、封面或不足以证明条件的抽帧时返回 unknown；本仓库未凭空配置真实视频模型。
- all：有 fail 即 fail，否则存在 unknown 为 unknown，否则 pass。any：有 pass 即 pass，否则存在 unknown 为 unknown，否则 fail。所有条件仍须完整返回，不能省略失败或未知分支。
- 适配器冻结确认条件，evaluator 修改传入字典不能改写聚合关系。无效返回、缺失已知证据或普通执行异常在通用 Worker 中进入 unknown；鉴权和陈旧运行错误继续抛出，不伪装成内容 fail。
- 注册表必须登记 verified 的 video 聚合能力、后端绑定和真实工件，且 evaluator 的 review/config digest 匹配当前确认条件。模型/实现发布仍走原工件检查，不复用图片检测器。
- 每次运行继续复核租户/所有人/项目、task/revision/generation、recipe/manifest/release 绑定；旧运行不得发布当前结果。

## 有效计数与持久化

`build_exact_result_set(..., whole_video_media_by_candidate=...)` 支持 video，要求全部候选都提供可信原视频映射，且 candidate ID、源版本及内容摘要一一匹配。缺少映射不能从向量命中数推算原视频数。

只选择验证通过的候选，再按确认的 exact-content 去重。多命中合为同一原视频；不同来源的相同内容也只选择一个交付代表，保留来源关系。失败/未知不进入有效数，未达数量保留 gap。暂不启用视频配额、图片指纹或未经验证的视频近重复能力。

原 SQLiteMultiRuleApprovalRepository 在首次发布及重启后重建时使用相同映射与校验；累积合并前重新授权历史输入并检查原视频版本冲突。重复作业复用已保存结果，不重复读取、调用模型或计数。结果仍为业务结果预览，不等同于已打包交付。

## 当前门禁与限制

- 默认不提供视频连接器、解码器或模型。仅服务端具备授权来源、已验证整段索引、文字编码能力、当前任务发布注册表和实际预检/检查器后，才开放公共确认执行。缺一项明确拒绝。
- 文件路径执行已接入原通用 Worker，支持受限临时盘、分块摘要和文件 evaluator；小文件字节兼容路径限 10 MiB。超限不能截断视频伪造通过；文件结束删除，回调必须自行设置解码和模型超时。
- 自动首批验证和补数使用原批准/结果账本，首搜回执保存恢复检查点。首次条件调用估算计入补数额度，不等于实际模型计费。预算不足/零候选以原因码暴露，不能记作足额完成。
- 合成测试覆盖身份、多命中、版本冲突、逻辑聚合、配方冻结、验证与计数、权限/旧运行、SQLite 恢复和幂等。真实视频质量、并发、性能及内网授权需单独验收。

## 小文件整段视频交付后端（兼容 @1）

WholeVideoDeliveryService 复用现有交付状态机、SQLite 原子暂存/发布、审计事件及 ZIP 下载协议；保留既有 image_deliveries/image_delivery_events 表名作为兼容存储，不复制视频专用状态账本、不迁移已有图片包。

- 输出 `whole-video-delivery@1`，文件为 `videos/0001.mp4` 或 `.webm` 与 `manifest.json`。原视频字节保持不变，不裁剪、不转码。只打包当前结果中已验证、去重并入选的原视频，不将失败、未知或备用候选混入交付。
- 清单包含 distinct_video、完整确认条件、来源结果/需求摘要、源命名空间/原视频 ID、规范媒体单元、版本、内容摘要、时长及容器类型；来源字段采取固定白名单。授权媒体引用、临时地址、检查器诊断不进入永久清单。
- 格式检查器是必填的服务端 callable，输入已授权读取且摘要匹配的完整 bytes，输出至少为 `{'media_type': 'video', 'container': 'mp4'|'webm', 'duration_ms': positive_int}`。实际实现必须核验完整视频容器与视频流，并自行落实解码时限与资源约束；文件扩展名、MIME 或任意输入字段不是检查依据。格式检查不是内容语义验证。
- 没有内置假检查器、真实模型或凭空猜测的内网协议。缺失检查器不能构建视频交付服务；检查失败、空/超限/摘要变化均不暂存包。当前每个视频沿用 10 MiB 上限，整个 ZIP 沿用 64 MiB 上限；不是大视频流式交付实现。
- 满额结果可进入打包；不足额结果必须已有合法持久停止记录和发起人决策门禁，不能将仍在运行的缺口任务提前标为交付完成。暂存不授予下载权，发起人明确确认发布后才可下载；完整/部分交付分别保留 COMPLETED/PARTIAL。
- 暂存、发布、下载继续复核当前任务/版本/generation、权限及结果绑定。发布与下载时重新读取授权素材并校验字节摘要、格式/时长等检查结果与暂存清单一致；源内容或检查事实漂移时拒绝。ZIP 名称、成员摘要、清单及整体摘要继续按原协议核验。
- 读取当前已发布候选时可由后端显式请求 `get_published_candidates(..., include_media=True)`，返回原公共 MediaRecord；默认返回候选及授权引用的旧接口行为保持不变。完整原视频身份在交付前再次校验。

平台 build_platform_services 已通过 MediaDeliveryService 按经鉴权的任务目标分流。可由受信 integration factory 提供可选 `inspect_video(app, content_bytes)` hook；未配置时图片保持原行为，视频交付 status 返回 available=false / VIDEO_DELIVERY_UNAVAILABLE，写操作明确拒绝。声明式 wire 配置不会把用户字段解释成可执行检查器。

## 文件模式与原视频引用交付（@2）

配置 `inspect_video_file(app, path)` 时使用 `whole-video-delivery@2` / `authorized_originals`。ZIP 中 `videos/0001.json` 保存原视频元数据和绑定结果摘要的鉴权下载路径；`packaged_content_digest` 校验 JSON 成员，`source.content_digest` 校验原视频，不混淆两者。原视频不写入 ZIP/BLOB，平台原视频接口按发布结果重新鉴权、检验原始身份/版本/摘要与检查事实，再从临时文件流式返回。

预览与原视频 GET 支持单一 Range；未发布、不合格、过期结果或撤权均不可下载。文件检查器返回字段同小文件协议，清单另含准确 byte_count。临时文件默认单个 100,000,000 字节，作业合计 500,000,000 字节，最长时长 3,600,000 毫秒；配置及硬上限见 [平台接入说明](../docs/dev/platform-integration.md)。`@1` 与图片历史交付仍可读。

文件 evaluator 接口为 `evaluate_file(path, candidate_ref, cleaning_binding, conditions)`，返回的 rows/evidence 与字节协议相同。文件在检测后、检查点前和上下文退出时复核摘要；不得保留或改写临时文件。实际视频模型与真实解码仍由部署方接入，合成字节和检查器测试不记作真实视频或内网验收。


## 手动检索与固定输入停止（2026-11-20）

当已确认的 retrieval 节点为 manual 时，视频正式确认不自动批准或执行检索。发起人选择并确认既有 first-search 计划后才开始；索引必须同时具备文字编码能力、项目授权及明确的 whole-video 粒度登记。视频首批计划最多 100 命中，并在计划准备与清洗准备分别复核确认预算。

`input_only` 的非足额验证结果发布时，既有 `refill_execution_stops` 表保存 `INPUT_SCOPE_COMPLETE`，任务进入 `AWAITING_INPUT`，结果、停止记录和状态原子提交。此状态表示输入范围已处理、等待发起人决定，不代表完成交付。通过现有交付暂存/确认发布后才进入 `PARTIAL`；重复发布不重复计数，历史读取保持原结果摘要。不会因不足额自行检索下一页或扩大来源。
