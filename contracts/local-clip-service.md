# 本地 CLIP 图文检索服务（可访问，图片类）

范围：本机可访问的 Chinese-CLIP 图文检索，图片类，500 张 Wikimedia Commons 测试图片。
这是**开发期可访问的真实 CLIP 服务**，不是内网 Milvus，也不是 `LOCAL_EXACT` 标签代理夹具。
它复用现有 `LocalCLIPEncoder` + `LocalVectorRetrieval` + `LocalFirstSearch(LOCAL_CLIP)` 进程内路径，
不切换 8085/8094，不把模拟结果算作联测通过。

## 1. 服务形态

进程内编码器 + 本地向量检索，**同一冻结模型**同时构建图片向量与编码文本查询：

- 模型：`OFA-Sys/chinese-clip-vit-base-patch16`（`model_type=chinese_clip`，`projection_dim=512`，`metric=COSINE`）
- 运行环境：独立 `.venv-clip`（已加入 `.gitignore`），`transformers 5.22.1` / `torch 2.15.0` / `pillow` / `faiss-cpu`
- 模型快照：本机 HF 缓存（公共模型，无凭据；真实内网凭据另放 `.env` / `*.key`，不入库）
- 编码器身份：`local-clip:<sha256[:24]>`，由 `config.json` + 权重 + tokenizer 全量摘要生成；
  `IndexBinding.encoder_ref` 必须与之一致，否则 `ENCODER_MISMATCH`（409）

> transformers 5 的 `get_text_features` / `get_image_features` 返回
> `BaseModelOutputWithPooling`（4.x 返回裸张量）。`LocalCLIPEncoder._normalized` 已处理：
> 命中 `pooler_output` 即取投影后的 512 维向量。**不要**对 `last_hidden_state[:,0,:]` 再做投影——
> `pooler_output` 已是 `text_projection` / `visual_projection` 的输出。

## 2. 索引配置（受信，不入库）

索引由 `dsh_retrieval.local_clip.build_local_clip_fixture` 构建，写入 `var/clip-index-commons-500/`（`var/` 已 gitignore）：

```
catalog.json        # local-catalog@1：library commons-test + binding commons-local-clip + grant tenant-a/user-a/demo
capabilities.json   # text-query-capabilities@1：binding 能力，filter_template_ref=test-public，max_batch_size=20
fixture.json        # local-first-search@1：500 条 records，每条含 512 维真实 CLIP 向量 + media 映射
```

`IndexBinding`（来自 `catalog.json`）：

| 字段 | 值 |
|---|---|
| `binding_id` | `commons-local-clip` |
| `library_id` | `commons-test` |
| `index_version` | `clip-<encoder_ref[-12:]>-<manifest_digest[:16]>` |
| `encoder_ref` | `local-clip:73fc251fb925db83377f2a80` |
| `vector_dimension` | `512` |
| `metric` | `COSINE` |

每条 `record.media` 携带 `media_unit_id` / `asset_version` / `source_namespace` /
`source_asset_id` / `authorized_media_ref` / `content_digest`（sha256）——即“命中→素材主键/版本/摘要”的受信映射源。

构建命令（凭据/路径在本机私有配置 `var/clip-index-commons-500/access.local.json`，不入库）：

```bash
PYTHONPATH=src .venv-clip/bin/python -m dsh_retrieval.local_clip \
  --manifest var/test-media-commons-500/manifest.json \
  --out var/clip-index-commons-500 \
  --model <HF snapshot path> \
  --batch-size 16
```

## 3. 两个接口契约

### 3.1 命中 → 素材主键 / 版本 / 摘要（`MediaRepository.resolve_hits`）

端口：`ports.MediaRepository.resolve_hits(context, hits) -> tuple[MediaResolution, ...]`

- 输入：`SearchHit(binding, vector_id, score)` 元组，按物理 binding + index_version + vector_id 解析，**绝不按 ID 单独解析**
- 输出：每条 hit 一条 `MediaResolution(hit, media, status)`，顺序与输入一致
- `MediaRecord`：`media_unit_id` / `asset_version` / `source_namespace` / `source_asset_id` /
  `authorized_media_ref` / `content_digest`（`sha256:<64hex>` 或 None）
- `status`：`resolved` / `unavailable` / `mapping_missing` / `mapping_conflict`
- 缺失/冲突保持显式，**不做模糊猜测**；未授权资源不暴露任何 media 元数据

本地实现：`LocalVectorRetrieval` 在构建时把 `record.media` 写入 `mappings[(binding, vector_id)]`，
`resolve_hits` 直接查表。HTTP CLIP 路径用 `CLIPMediaMappings.resolve(match)`（`clip-media-mappings@1` 受信配置）。

### 3.2 授权读取原图（`MediaAccessGateway.open_read`）

端口：`ports.MediaAccessGateway.open_read(context, media_ref, max_bytes) -> ContextManager[BinaryIO]`

- `media_ref` 来自受信映射服务，**不是**模型/客户端提供的任意 URL 或路径
- 每次调用重新授权（`AuthorizationService.require_access(context, media_ref, "read")`）；
  旧 `authorization_version` 不是永久授权
- 流式读取，强制 `max_bytes` 上限；`purpose` 必须是 `cleaning_input` / `candidate_preview` / `delivery_export`
- 读取前后校验 SHA-256 与 `MediaRecord.content_digest` 一致，**不一致即失败关闭**（`INPUT_SNAPSHOT_CHANGED`）

本地实现：`LocalTestMediaCorpus.open_read`（校验 manifest、project_id==demo、SHA-256）。
`LocalCandidateMaterializer.materialize` 把上述两个接口串起来：解析命中 → 逐图授权读取 →
有界临时文件 → SHA-256 复验 → `MaterializedPage`（文件随 context 退出而失效）。

## 4. 测试权限

- 租户/用户/项目：`tenant-a` / `user-a` / `demo`（`catalog.json` grant）
- `filter_template_ref`：`test-public`（scope `public`，服务端拥有，非用户表达式）
- 用途授权：`cleaning_input` / `candidate_preview` / `delivery_export`
- 媒体引用：`media:local-test:<source_asset_id>@<asset_version>`
- 隔离账本：`tools/verify_local_clip_500.py` 用 `TemporaryDirectory` + 独立 SQLite，不触碰既有任务/用户账本

## 5. 真实图片端到端验证

`tools/verify_local_clip_500.py`（`PYTHONPATH=src:tests`，`.venv-clip`）：

```bash
PYTHONPATH=src:tests .venv-clip/bin/python tools/verify_local_clip_500.py \
  --index var/clip-index-commons-500 \
  --manifest var/test-media-commons-500/manifest.json \
  --model <HF snapshot path> \
  --query "人与宠物合影"
```

已跑通（2026-10-31）：

```json
{"query":"人与宠物合影","mode":"LOCAL_CLIP","search_stopped":true,
 "candidate_count":5,"first_preview_type":"image/jpeg","first_preview_bytes":160967,
 "verified_manifest_count":5,"accepted_count":null}
```

即：真实 CLIP 文本编码 → 本地向量检索 → 命中解析为 `MediaRecord` → 授权读取原图（160967 字节 JPEG）
→ SHA-256 复验 → 候选预览，全链路真实图片，未切换 8094。

## 6. 已知边界（不隐瞒）

- **检索质量低于预期**：Chinese-CLIP base 在本 500 图语料上的零样本召回偏低
  （单字查询 recall@10 多为 0–1/10，图→图 recall@10 约 1–6/10）。链路契约与授权读取已验证为真，
  但**语义排序质量不达标**，不能据此宣称检索效果验收通过。可能原因：transformers 5 对该 checkpoint
  的视觉投影加载、tokenizer/processor 版本、或 base 模型本身容量。需后续排查（见第 7 节）。
- 这是**本地公共模型 + 公共测试图片**，不是真实内网凭据/数据；模拟/本地通过 ≠ 真实内网联测通过。
- `LOCAL_CLIP` 路径独立于 8085/8094 主链路；切换 8094 前须先解决检索质量并完成真实内网联测。

## 7. 下一步（检索质量排查，不阻塞契约交付）

1. 对比 `clip_cn_vit-b-16.pt`（OFA 原始 cn_clip checkpoint，`module.visual.proj` / `module.text_projection`）
   与 HF `pytorch_model.bin` 的视觉投影权重是否一致；确认 transformers 5 的 `visual_projection` 加载正确。
2. 用 OFA 官方 `cn_clip` 推理库对同一批图片编码，与 `LocalCLIPEncoder` 输出比对，定位差异来源。
3. 若确认是 transformers 5 加载问题，回退 `transformers<5` 或改用 OFA 原生推理；更新 `.venv-clip`。
4. 质量达标后再接 VLM 复判（`--vlm-settings` 路径）与真实内网联测。
