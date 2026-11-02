# 本地模型设置

本次实现服务端个人连接注册、密钥更新、模型目录获取、手动模型绑定、默认 LLM/VLM 和显式能力测试。
继续复用现有 DSH LLM / 直连 VLM 执行链，没有新建平行 Agent。原 `--model-config` 模型仍可选。

## 启动与信任边界

在现有启动参数上增加：

```sh
--model-settings-store /private/local-state/model-settings.sqlite3 \
--model-key-file /private/local-secrets/model-settings.key \
--model-allowed-url https://gateway.example/v1
```

以上均为示例地址。URL 参数可重复；现有受信 `--model-config` 的 Base URL 自动加入允许列表。
URL 必须为精确的 HTTPS Base URL，不包含 `/models`、`/chat/completions`、查询参数或凭据。
注册操作不能修改允许列表，URL 更换需创建新连接。只能注册管理员认可的模型网关，
不支持从网页任意访问内网。发现接口禁止重定向、不使用环境代理、有超时和响应大小限制。
DSH 自身的网络出口仍需部署层白名单；管理员批准网关不等于隔离恶意网关的全部网络行为。

当前仍为 **loopback 本地模拟身份模式**，不是生产多用户认证：仅接受 localhost/127.0.0.1 Host，
配置写入校验 Origin 和 JSON Content-Type，并校验已有项目授权。
连接和默认值按 `(tenant_id, user_id)` 隔离，是个人配置，不是项目共享凭据。
不得将这个使用模拟身份头的服务暴露到公网或当成生产权限认证。

## 密钥保存与恢复

使用系统 `/usr/bin/openssl` 的 AES-256-CBC + PBKDF2（100000 次 SHA256），
以独立派生的 HMAC-SHA256 密钥先认证、后解密；密文绑定连接 ID 和个人身份。
OpenSSL 密码通过继承的管道传递，不进入命令行、环境变量或临时明文文件。
运行系统需具备支持上述参数的 OpenSSL/LibreSSL（本机 LibreSSL 3.3.6 已验证），未安装新依赖。

主密钥随机生成 32 字节，要求仓库外、当前用户所有、0600、非符号链接。
SQLite 文件为 0600，仅凭据字段加密；模型名、URL 和连接名属于明文元数据，不应填入凭据。
主密钥和 SQLite 应分别备份。已存在的非空配置库缺少主密钥时拒绝启动，不生成替代密钥。
主密钥丢失意味着无法恢复原 API Key，必须重新注册；此实现不是托管 KMS。
API Key 永不通过读接口回显，页面清空密码输入，不放入 localStorage/sessionStorage 或对话。

## 页面流程

1. 右侧“配置模型服务”打开独立弹窗；可新建连接，也可直接选已保存连接。后者显示加密保存状态，刷新目录和绑定模型无需再输入密钥。
2. 保存失败时在弹窗顶部显示错误并保留输入；保存成功后清空密钥。目录获取失败会说明“连接已保存”，可刷新重试，无需再次注册。
3. 弹窗只管理连接和可选的手动模型 ID。关闭弹窗后，在右侧搜索并选择当前 Chat 的 LLM/VLM；首次选择目录模型会建立角色绑定。
4. 右侧仅有一套个人默认 LLM/VLM，搜索选择后保存。默认模型只作用于新 Chat；当前 Chat 的选择独立保存。
5. “测试 LLM/VLM”显式调用当前选择的模型，可能产生费用。LLM 经当前运行引擎测试 JSON；
   VLM 使用应用内生成的红色色块测试图像输入，不上传业务素材。成功仅证明最小测试通过。

模型目录遵循 [OpenAI List models](https://developers.openai.com/api/reference/resources/models/methods/list)
的 `data[].id` 格式。目录不承诺调用权限/余额、视觉能力或 DSH 工具能力。
不支持服务商私有分页协议；发现分页时返回明确错误并保留原目录，支持手动 ID。
标准目录最大 1 MB / 5000 个模型。每人最多 50 个连接，每连接最多绑定 100 个角色模型。
同一模型可分别绑定 LLM 和 VLM。未实现其它协议适配、连接删除、跨用户共享或自动模型回退。

历史正式任务恢复其已确认版本的模型绑定；恢复草稿采用草稿中的绑定；当前对话 sessionStorage
保存模型 ID（无密钥）。旧版对话若没有模型记录，则仍按旧行为加载，需要用户确认当前选择。
密钥更新不更改模型 ID/config digest；URL/模型更换不会覆盖旧绑定。默认模型由 UI 在新 Chat
选择，API 调用方仍须显式传入模型 ID，服务端不悄悄替换模型。

## API（均位于 `/v1/retrieval/projects/{project}/model-settings`）

| 方法 / 相对路径 | 请求 / 用途 |
| --- | --- |
| GET 根路径 | 返回脱敏连接、个人 defaults、管理员批准 URL |
| POST `/connections` | `{name, provider: "openai-compatible", base_url, api_key}` |
| POST `/connections/{id}/discover` | `{}`；刷新标准模型目录 |
| POST `/connections/{id}/bind` | `{model, kind: "llm"或"vlm"}`；返回稳定 profile_id，重复绑定幂等 |
| POST `/connections/{id}/rotate` | `{api_key}`；更新密钥并清除旧测试记录 |
| POST `/defaults` | `{llm: profile_id或空字符串, vlm: profile_id或空字符串}` |
| POST `/probe` | `{profile_id, kind}`；失败不改变默认值 |

已有 GET `/models` 增加个人动态 profiles 与 defaults，兼容旧字段。
连接注册失败可重试；注册本身非幂等，成功后请选已有连接而不是重复注册。
