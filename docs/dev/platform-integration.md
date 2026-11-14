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
