# 平台接入实现与部署

本文件对应根目录《检索Agent平台接入契约_v0.md》。它描述代码装配和验证边界，不代表平台接口、W3/PAT、真实素材及生产可靠性已经验收。

## 已实现

- `platform_server` 是独立的平台启动入口，强制持久化账本、独立凭据存储和服务端租户命名空间，不安装 PostgreSQL，不加载本地 demo/fixture 作为权限兜底。
- 平台身份通过 `/user/info` 核验，项目 API 强制单一规范 `x-app-id`，路径、任务和子记录通过业务层项目门禁核对。只有发起人能修改和确认；Chat、草稿、任务和未共享结果默认私有，不新增跨项目共享或多人会签。
- 现有页面在认证与项目选择完成后加载业务脚本。统一网络层去掉开发身份头，注入当前项目和 CSRF；浏览器存储按租户/用户/项目隔离。切项目中止请求并重新加载，不携带原任务参数。页面每30秒复核资格，401/403或复核失败清除展示；每个业务操作仍实时鉴权。
- 浏览器使用 Secure/HttpOnly/host-only/SameSite=Lax 应用会话，不在 JS、URL、localStorage 中保存 PAT/refresh。写请求校验 CSRF。退出仅删除当前会话，不退出 W3、不自动撤销已批准后台任务。
- refresh 委托使用已有 SecretBox 的加密和完整性校验机制，按用户关联数据绑定；主密钥须在仓库外。PAT 仅内存保存，提前轮换、单进程串行换发、失败退避，不复用过期凭据，不回退服务身份。重启读取加密委托重新取得 PAT，并验证仍属于原用户。没有可用委托则要求重新认证。
- 目录/检索使用用户权限与当前项目授权的 DatasetVersionRef 交集；物理 binding 必须登记。召回前必须传版本过滤，命中返回后复验映射及素材权限，缺摘要或映射错误拒绝。
- 分页游标以持久密钥派生的 HMAC 绑定人、项目、任务、版本/generation、计划请求和授权版本集；换项目、换计划或授权集变化不能续页。
- 临时媒体地址由平台接口产生，HTTPS 精确 origin 白名单、禁重定向、不向对象存储传 PAT/cookie，声明及实际字节双重限制。读取前后复验当前授权，候选预览复验内容摘要；后续清洗/交付沿用原业务摘要和版本检查。
- `PlatformFirstSearch` 复用既有计划批准、候选清单和业务状态逻辑；默认装配清洗、补数、自动入选及交付服务，平台侧只提供数据协议转换。正式任务/批准/幂等/outbox 和结果关系保存在原业务账本，不将平台批跑任务作为草稿账本。

## 私有部署配置

复制 `config/platform.example.json` 到仓库外的受控目录，填写真实环境配置。示例域名与路径不可直接联调。所有状态文件、主密钥和 DSH_HOME 分开；当前默认单服务实例，不让多个进程共同使用相同凭据库和执行目录。真实多实例部署须另做凭据换发协调及任务租约的集成验证。

```sh
PYTHONPATH=src python3.13 -m dsh_retrieval.platform_server \
  --config /absolute/private/platform.json --port 8087
```

`--integration` 默认是内置 `dsh_retrieval.platform_protocol:create`，读取 `protocol_config` 指定的私有 JSON；结构模板见 `config/platform-wire.example.json`，其中未确认占位符必须替换，不能直接启动。它只做声明式字段转换，不能执行脚本。已核验的专用适配器也可由管理员通过 `--integration package:factory` 注入；浏览器和模型无权选择模块。

可选的 `model_config` 使用现有模型配置格式；启用 DSH 时同时明确填写 `dsh_python` 与独立 `dsh_home`，入口校验安装并恢复运行记录。不安装依赖或修改 vendored DJ。未配置模型时模型功能保持不可用。

服务仅监听 loopback，前方须有受信 HTTPS 反向代理。代理应限制可访问路由、清除外部伪造身份头；浏览器最终访问同站 HTTPS 应用地址。iframe 只允许 `frame_origins` 中的精确 HTTPS origin。跨站 iframe 的第三方 Cookie 不在本版默认支持范围；使用平台同站域名/代理，不能静默去掉 Secure 或放宽 CSRF。当前页面使用根路径，部署时须保留其路由，不能直接挂在任意子路径后假定可用。

平台在用户完成实际 W3/PAT 配对后，向应用 HTTPS `POST /v1/platform/session` 传个人 Bearer；JSON 仅可含 `refresh_key` 和不超过3600秒的 `expires_in`。该请求必须由浏览器同站网关流程接收 Set-Cookie；纯服务间调用拿到 Cookie 不会自动让用户浏览器登录。无 refresh 委托的登录不承诺重启后继续后台访问。Agent 不实现猜测的 W3 登录回调，也不持有平台 SECRET_KEY。

## 内置 wire 配置格式

顶层严格使用：`schema="platform-wire@1"`、`user_id_path`、`operations`、`libraries`、`capabilities`、`media_origins`。`user_id_path` 是 `/user/info` 的 data 内稳定用户主键路径，必须按脱敏样例确认。

`libraries` 为现有 Library/IndexBinding 注册结构，`capabilities` 为 TextQueryCapability 结构；仅登记经过验证的索引、编码器和过滤模板。静态登记不授予访问权限，访问仍取决于实时项目/个人授权交集。平台查询默认支持文字输入检索；可选 `encode_reference` operation 在实际跨模态协议验证后开放。未配置时隐藏参考图入口并明确拒绝相应查询，不能改写用户查询方式。

每个 operation 包含 `path`、`method`（GET/POST）、`body`、`fields`；列表还须有 `items_path`；分页列表另需 `total_path`、`page_parameter`。已核验为完整无分页数组的接口显式设置 `unpaged=true`，不能因缺少 total 自动推定无分页。`path` 必须是已核验的相对 API 路径，不接受占位尖括号。所有路径由管理员配置，不能来自模型或用户输入。实际调用以 `api_base_url` 为前缀。

- `body` 是 JSON 模板。完整字符串 `$project_id`、`$user_id`、`$refresh_key`、`$versions`、`$request.vector.values` 等替换为受信运行变量；不支持表达式、SQL、任意代码或字符串拼接。
- `fields` 将内部字段映射到响应 data 中的点分路径，例如 `{"version_id":{"path":"version.id","type":"string"}}`。可选类型为 string/integer/number；`values` 做显式枚举转换，未知值失败；`{"literal":"live"}` 可声明经核验的保守服务能力，不得虚报 snapshot。
- 分页列表必须完整读取，超过100页、空页未达 total 或 total 变化会失败，不把部分权限集当成完整授权依据。

| operation | 运行变量 | 规范化响应字段 |
|---|---|---|
| exchange | refresh_key；固定POST，不携带用户PAT | token、expires_in；PAT最长3600秒 |
| projects | user_id、分页参数 | 每行 id、name、alias（可省alias） |
| project_datasets | project_id、user_id、分页参数 | 每行 library_id、version_id |
| user_datasets | user_id、分页参数 | 每行 library_id、version_id |
| encode_query | project_id、text、binding | encoder_ref、values |
| encode_reference（可选） | project_id、image_base64、binding；图片来自授权上传记录并核对摘要 | encoder_ref、values |
| search | project_id、versions、request（SearchRequest） | hits、next_cursor、stop_reason、consistency、snapshot_ref |
| resolve_hits | project_id、hits（完整SearchHit数组） | 每行 vector_id 及 MediaRecord 全部字段 |
| media_version | project_id、media_ref | library_id、version_id |
| preview | project_id、media_ref | url |

search 额外要求 `version_scope_path` 指向 body 中的版本过滤值；代码在发请求前要求其与 `$versions` 完全一致。不能只传项目标签并在召回后过滤。`hit_fields` 将 hits 每行转为 vector_id/score；未配置时要求同名字段。stop_reason 仅可为 more_available/exhausted/depth_limit，consistency 仅可为 live/snapshot，按实际服务能力映射。数据集版本不是向量索引版本，两者分别登记和验证。

resolve_hits 的 `items_path` 指向与输入等长、同序的映射列表，每行须返回相同 vector_id；MediaRecord 包含 media_unit_id、asset_version、source_namespace、source_asset_id、authorized_media_ref、content_digest。摘要为 `sha256:` 加64位小写十六进制。不可用或缺失映射拒绝发布，不伪造可读取素材。

默认所有操作访问同一受信平台 API origin；如内部编码/向量服务必须另行访问，通过经过审查的专用 integration factory 实现同一套端口，保留项目过滤及版本语义，不把访问地址交给模型决定。

## 当前验证和外部进入条件

本地测试使用合成身份、脱敏结构与合成字节，覆盖真实业务账本及装配，但没有访问内部平台。需要平台交付已确认的 PAT 支持、账号停用/吊销语义、当前项目授权版本映射、实际检索及预览响应，才能填写 wire 配置并运行内网定向验收。固定普通测试用户、测试项目与测试素材也须由平台准备。

本仓库实现不要求这些未知字段通过“猜一个值”继续运行。配置缺失、协议错误、无个人委托或权限服务故障均拒绝受限操作。完成本地开发不能记为 W3/PAT 接通、真实素材授权通过或 v1.9 全量验收。


## 整段视频执行与文件接入

视频默认不可执行。受信 integration factory 可在原 `capabilities` / `encode_query` / 检索、解析与授权读取协议之外提供：

| Hook | 返回与责任 |
| --- | --- |
| `whole_video_bindings(app)` | 精确 `IndexBinding` 序列；声明已核验为整段原视频的索引，不能登记帧或片段索引 |
| `video_preflight(app, actor, project_id, spec_or_none)` | 实际核验文字编码、索引、原视频读取、解码和条件验证协议；只有 `{'ready': True}` 放行，能力查询时 spec 可为 None |
| `video_registry(app, actor, task)` | 当前任务绑定的 `ReleasedDetectorRegistry`，包含 verified 视频能力与经摘要核验的发布工件 |
| `inspect_video_file(app, path)` | 在受限临时文件上实际核验容器、视频流和时长，返回 `media_type=video`、`container=mp4/webm`、正整数 `duration_ms` |
| `inspect_video(app, bytes)` | 兼容小文件检查器；只在不超过 10 MiB 时使用字节路径 |

文件型 evaluator 用 `WholeVideoConditionDetector(task, evaluate, evaluate_file=...)` 接入。文件 callback 不得缓存临时路径，须自己约束解码/模型调用资源并保存可追溯证据；路径在本次处理结束后删除。`preflight` 是受信服务端健康检查协议，不能填固定 ready 冒充真实核验，也不能从前端开启。

配置可选 `video_resources` 对象：`max_video_bytes` 默认 100,000,000、`max_job_bytes` 默认 500,000,000、`max_duration_ms` 默认 3,600,000、`read_seconds` 默认 120；均为正整数。单视频硬上限 2 GiB、单作业硬上限 8 GiB，进程同时最多两个视频临时文件读取。HTTP 上游仍须设置自身网络超时；本策略不强制终止不返回的第三方 callback。资源限制是服务端配置，不由草稿修改。没有安装默认解码器或模型。

文件模式交付使用 `whole-video-delivery@2`：ZIP 保存原视频清单和鉴权下载路径，原视频经发布后的接口流式读取，不把大视频塞入 SQLite BLOB。原始内容、版本、条件与结果摘要仍绑定当前发布结果。旧小文件 `whole-video-delivery@1` ZIP 保持可读；图片清单不变。

平台浏览器视频采用带项目的 GET 路径 `/v1/retrieval/projects/{project}/tasks/{task}/...`，从已有安全 Cookie 验证身份，再按 URL 项目重新授权；仅这类严格匹配的视频只读路径可推导 `x-app-id`。不把 token 放进 URL，不放宽 POST、CSRF 或跨项目校验。支持单一 Range、206/416；读取中断后浏览器重试仍须重新鉴权和校验当前结果。开发模式继续使用请求头鉴权。

升级后旧平台搜索游标不再复用，需重新准备批准计划。新游标绑定用户、项目、任务、revision、generation、批准计划、索引与授权范围；正常清洗引发的 task_version 更新不使同一计划续页失效。旧运行仍被 revision/generation/计划栅栏拒绝。

联调前必须提供实际源索引粒度、文字/视频 embedding 对齐信息、不可变原视频身份与版本摘要、授权读取协议、真实检查器及带证据的验证器。合成测试不代表内网、真实解码或质量验收通过。


## 手动视频计划与固定输入

已确认需求的 `node_modes.retrieval=manual` 时，平台后台首搜消费回执为 `status=manual`，不调用 auto_start。操作入口沿用现有接口：GET `tasks/{task}/first-search` 读取授权来源，POST `/first-search/prepare` 提交 routing_digest 与每分支所选 binding_id/batch_size，POST `/first-search/confirm` 提交 proposal_digest、expected_task_version 和 action=confirm。准备不执行，确认使用 Idempotency-Key，后台执行器随后处理首批。无需新增内网 API。

视频计划准备与清洗准备分别检查容量及确认预算。固定输入任务只处理已确认的首批窗口，缺口按 INPUT_SCOPE_COMPLETE 原子持久化并进入 AWAITING_INPUT；发起人确认交付后才标记 PARTIAL。视频进度可以在同 revision/generation 的清洗及交付后读取，但只读路径不会授予续页执行权限。图片默认行为不变。

## 多账号隔离补强与当前启用边界（2026-11-21）

已选择接现有数据平台登录，不另建账号/密码主库。沿用平台核验个人凭据、项目授权、HttpOnly会话及租户/用户/项目存储命名空间。

- 身份认证拒绝重复Authorization；浏览器桥接拒绝重复Cookie、页内用户及CSRF头、同名会话Cookie，避免不同组件取不同身份。
- 当前上下文、资格复核和退出均核对页内用户与真实会话用户；旧页不能退出刚切换的新账号会话。
- 页面展示已核验用户ID；定时、重新获得焦点、恢复浏览器页面及重新可见时复核身份、会话CSRF和项目资格。失败即中止请求、清除旧展示；失效后的晚到响应不再交付业务脚本。每次业务请求仍由服务端鉴权，页面复核不是授权缓存。
- 双账号HTTP并发测试覆盖同幂等键分别创建私有草稿、本人读取成功、跨用户读取/确认拒绝。Node覆盖用户切换、会话变化、项目移除、撤权及切项目晚到响应，不以模拟平台测试替代真实平台联测。

当前8096仍是开发演示装配；没有真实平台私有配置与HTTPS登录入口时不自动切换，不能宣称真实多用户已上线。需管理员提供仓库外配置文件路径（结构参考`config/platform.example.json`、`config/platform-wire.example.json`），包含已核验API及协议映射、独立平台账本/委托存储和密钥位置，并由HTTPS反向代理转发平台登录会话建立流程。不要将Token、密钥或真实地址写入仓库。既有演示任务不自动转移给真实账号；如需迁移应另行明确所有者映射并验收。
