================================================================================
CLIP 图文搜索服务・信息对齐文档

---

对齐代码 : D:\myproject\Code\clip-image-search\Milvus\app_resident_search.py（1645 行）
FastAPI 单进程服务（uvicorn.run，无 workers 参数），默认 0.0.0.0:4444
服务标题 : "CLIP 图文搜索（常驻切片 + 动态数据集）"
填写规则：仅填写代码中真实存在的信息；不存在的一律标注 [代码中未定义] /
[代码中不存在]；[示意] = 按代码真实字段结构构造的示例，数值均为虚构
================================================================================

一、向量库结构

---

1.1 产品与连接
产品      : Milvus（pymilvus.MilvusClient，uri = 本地 .db 文件路径，本地文件模式）
版本      : [代码中未定义]（未声明 pymilvus / Milvus 版本）
连接参数  : timeout=30（DB_CONNECTION_TIMEOUT）、
grpc_keepalive_time_ms=300000、grpc_keepalive_timeout_ms=10000
加载方式：每个切片 (.db) 各建一个 MilvusClient，调用 load_collection (collection_name)
database  : [代码中不存在 database 概念]（未使用 db_name 参数）
1.2 collection 命名
来源：数据集 config.json 的 "collection_name" 键；缺省取环境变量
DEFAULT_COLLECTION_NAME，默认值 "xmage_chinese_clip_vit_huge_patch14"
容错      : safe_json_load 自动修复 config.json 中无引号的 collection_name 值
（如 collection_name: caigou_4K → "caigou_4K"）；解析失败返回 {}
（config.json 从数据集目录向上最多查找 4 层）
1.3 主键 / 向量字段 / 维度 / 距离
主键字段  : [代码中未定义]（本服务只读，未声明、未读取主键）
向量字段  : [代码中未定义]（检索未指定 anns_field，使用 collection 默认向量字段）
向量维度  : [代码中未定义]（由所加载模型 chinese-clip-vit-huge-patch14 决定）
距离类型  : metric_type = "IP"（内积；查询与库内向量均为 L2 归一化向量）
索引参数  : ef_search=128（env HNSW_EF）、m=16（env HNSW_M）（HNSW 系索引参数）
1.4 本服务实际读取的 schema 字段
path        : 图片完整路径（entity ["path"]）
folder_name : 文件夹名（entity ["folder_name"]，用于过滤与文件夹列举）
1.5 top-k
默认 500（DEFAULT_MAX_RESULTS；max_results 传 0 / 负数 / 非法值时回落 500）
上限 1000（MAX_MAX_RESULTS，超出截断）
执行方式：每个切片各检索 top max_results（Milvus limit，offset 恒为 0），
合并后按 similarity_score 降序、按 full_path 去重，取前 max_results 条
1.6 分页
Milvus 层：不分页（offset=0 一次取回）
API 层：完整结果缓存在服务端（search_cache，TTL 600s），按 group_size
（默认 25，范围 1~200）/page（默认 1）切片返回；
翻页命中缓存时 served_from_cache=true
1.7 过滤字段
文件夹过滤 : folders 参数（逗号分隔）→ filter: folder_name in ["A", "B"]
文件夹列举 : GET /folders → filter: folder_name != ""，limit=10000（FOLDER_QUERY_LIMIT）
其他过滤   : [代码中未定义]（仅支持 folder_name）

二、Embedding 接口契约

---

2.1 接口形态
形态：服务内置编码模块 EmbeddingService（ChineseCLIP）
独立 API  : [代码中不存在]（无 /embed 等独立 HTTP 接口，仅服务内部调用）
2.2 模型
类        : transformers.ChineseCLIPModel + AutoProcessor
模型目录  : env MODEL_DIR，
默认 /mnt/DATA/f60055380/clip_vit/models/chinese-clip-vit-huge-patch14
模型标识  : "chinese-clip-vit-huge-patch14"（检索响应 model 字段固定值）
版本      : [代码中未定义]
2.3 设备与精度
设备      : env CLIP_DEVICE，默认 "cuda:0"；CUDA 不可用时自动回退 "cpu"
精度：非 cpu 设备 model.half ()（FP16）+ torch.inference_mode ()
2.4 文本编码
预处理    : processor (text=..., return_tensors="pt", padding=True,
truncation=True, max_length=77)（77 token 截断，无其他清洗）
前向      : text_model (input_ids, attention_mask)
→ last_hidden_state [:, 0, :]（CLS）→ text_projection → L2 归一化
2.5 图片编码
预处理    : PIL Image.open (io.BytesIO (字节)).convert ("RGB")
→ processor (images=..., return_tensors="pt")
前向      : vision_model (**inputs)
→ last_hidden_state [:, 0, :]（CLS）→ visual_projection → L2 归一化
解码失败  : ImageDecodeError ("无法处理图片: ...") → HTTP 422
2.6 输出
向量      : L2 归一化 float 列表；维度 [代码中未定义]（由模型结构决定）
2.7 批量与并发
文本微批  : 64（TEXT_BATCH_SIZE），攒批窗口 0.01s
图片微批  : 16（IMAGE_BATCH_SIZE），攒批窗口 0.01s
GPU 并发  : GPU_WORKERS=2（线程池），模型前向由 _gpu_lock 串行
2.8 输入限制
图片大小  : ≤ 20MB（MAX_IMAGE_BYTES，超出 HTTP 413）
图片像素  : Image.MAX_IMAGE_PIXELS = 100,000,000（PIL 保护，超出按解码失败 422）
文本长度  : [代码中未定义]（仅 77 token 截断 + 非空校验）
2.9 缓存
文本向量  : key=sha1 (文本)，TTL 3600s，容量 4096
图片向量  : key=sha1 (图片字节)，TTL 1800s，容量 512
2.10 超时 / 错误码 / 鉴权
超时      : [代码中未定义 embedding 专属超时]
错误码：仅 ImageDecodeError → 422，无其他 embedding 专属错误码
鉴权      : [代码中不存在]（embedding 不对外；搜索接口亦无鉴权）

三、索引目录

---

3.1 载体
目录存储  : [代码中不存在独立索引目录 / 注册中心配置]
运行时以 DatasetSpec 对象描述每个数据集（= 一个可检索索引），
可通过 GET /datasets、GET /health 实时查看
3.2 单个索引（DatasetSpec）字段 → 模板字段映射
library_id    : [代码中不存在]
最接近概念 : key = "{dataset_dir}::{sha1 (切片路径列表)[:12]}::{version}"
binding_id    : [代码中不存在]
最接近概念 : dataset_dir（数据集目录绝对路径）
index_version : [代码中不存在]
最接近概念 : version = "{切片最大 mtime_ns}:{切片总字节数}"
name          : "{project}_{目录名}"（无 project 时为目录名）
project       : 由路径推断（含 DB_milvus 段时取其上级 / 上上级；否则取父目录名）
collection_name : 见 1.2
shards /shard_paths : 选中切片 (.db) 文件名列表与绝对路径列表
image_root_dir : env DEFAULT_IMAGE_ROOT_DIR 默认 "/DATA"；config.json
"image_root_dir" 可覆盖（用于 web_path 映射）
web_prefix    : 默认 "/local_images/{name}"；config.json "web_prefix" 可覆盖
size_bytes /size_gb : 选中切片文件大小合计
pinned        : 是否常驻（常驻数据集 pinned=true，不可被内存淘汰、不可 unmount）
3.3 用途 / 媒体类型 / 别名 / 模型 / 角色权限
用途          : CLIP 图文搜索（图片素材以文搜图 / 以图搜图）
媒体类型：仅图片素材（文本仅作查询输入，不入库）
别名          : [代码中不存在]
embedding 模型: chinese-clip-vit-huge-patch14（全局单模型，全部索引共用）
项目          : project 字段（路径推断；env DEFAULT_PROJECT 默认 "xmage"）
角色 / 权限   : [代码中不存在]
3.4 切片选择规则
优先级：显式 shard_paths 参数 > config.json "active_shards"

> 
> 按 (mtime, 切片编号) 降序取最新 DEFAULT_SHARDS_PER_DATASET=2 个
> 上限：显式指定最多 MAX_SHARDS_PER_DATASET=4 个，超出报错
> 3.5 config.json 实际读取的键
> collection_name /active_shards/image_root_dir /web_prefix
> 3.6 常驻与动态挂载
> 常驻路径      : env DEFAULT_RESIDENT_PATHS（逗号分隔，可多条）；未设置时回退
> DB_ROOT_TEMPLATE.format (project_name=DEFAULT_PROJECT)
> 默认 /DATA/f60055380/clip_vit/milvus_db/{project_name}/DB/DB_milvus
> 路径白名单    : env ALLOWED_DB_ROOTS，默认 /DATA/f60055380/clip_vit/milvus；
> 一切加载路径必须位于白名单下，否则 404
> 内存上限      : MAX_LOADED_GB=350；超限淘汰最久未使用的非 pinned 且空闲 (ref=0) 数据集
> 挂载 / 卸载     : POST /datasets/mount、POST /datasets/unmount（后者需 Admin）
> 加载并发：全局同时加载数据集 2（MAX_CONCURRENT_LOADS）；
> 单数据集并行连接切片 2（LOAD_PARALLEL_PER_DATASET）

四、命中到素材的映射

---

4.1 映射链路
Milvus 命中 entity (path, folder_name, distance) → 组装为响应 matches [] 元素
vector_id /media_unit_id/asset_version / 来源系统 / 授权媒体引用 / 内容哈希
: [代码中均不存在]
4.2 match 元素字段（实际返回）
id               : 合并去重后的顺序号（从 1 开始）
similarity_score : round (IP distance, 4)；低于 min_similarity 的命中被丢弃
（min_similarity 文本默认 0.1、图片默认 0.2，可传参覆盖）
filename         : full_path 的 basename
full_path        : entity ["path"] 原值（素材存储完整路径）
web_path         : full_path 以 image_root_dir 开头时
= web_prefix + "/" + 去掉 image_root_dir 后的相对后缀；
否则 = full_path（供外部 Web 服务按前缀映射访问图片）
folder           : entity ["folder_name"]
app_name         : 切片 .db 路径按 "/" 分割的第 5 段（parts [4]）
dataset_name     : 所属数据集 name
dataset_key      : 所属数据集 key
shard            : 命中所在切片 .db 文件名
4.3 去重
跨切片按 full_path 去重（同一素材仅保留相似度最高一条），全局按相似度降序
4.4 素材读取
本服务不返回图片内容，仅返回 web_path 引用；
素材读取接口 : [代码中不存在]（需外部按 web_prefix 部署的静态 / 文件服务）

五、脱敏样例

---

说明：代码中不含任何内置样例 / 模拟数据；以下按代码真实字段结构构造，值均虚构。

5.1 模拟索引（数据集）× 2 [示意]
---------- 索引 1 ----------
{
"key": "/DATA/f60055380/clip_vit/milvus_db/xmage/DB/DB_milvus::9f8e7d6c5b4a::1698451200000000000:62914560000",
"dataset_dir": "/DATA/f60055380/clip_vit/milvus_db/xmage/DB/DB_milvus",
"name": "xmage_DB_milvus",
"project": "xmage",
"collection_name": "xmage_chinese_clip_vit_huge_patch14",
"shards": ["shard_0.db", "shard_1.db"],
"shard_paths": ["/DATA/f60055380/clip_vit/milvus_db/xmage/DB/DB_milvus/shard_0.db",
"/DATA/f60055380/clip_vit/milvus_db/xmage/DB/DB_milvus/shard_1.db"],
"image_root_dir": "/DATA",
"web_prefix": "/local_images/xmage_DB_milvus",
"size_gb": 58.59,
"version": "1698451200000000000:62914560000",
"pinned": true
}
---------- 索引 2 ----------
{
"key": "/DATA/f60055380/clip_vit/milvus_db/demos/DB/DB_milvus::1a2b3c4d5e6f::1698364800000000000:31457280000",
"dataset_dir": "/DATA/f60055380/clip_vit/milvus_db/demos/DB/DB_milvus",
"name": "demos_DB_milvus",
"project": "demos",
"collection_name": "caigou_4K",
"shards": ["shard_0.db"],
"shard_paths": ["/DATA/f60055380/clip_vit/milvus_db/demos/DB/DB_milvus/shard_0.db"],
"image_root_dir": "/DATA",
"web_prefix": "/local_images/demos_DB_milvus",
"size_gb": 29.30,
"version": "1698364800000000000:31457280000",
"pinned": false
}
5.2 虚拟向量记录（命中记录）× 每索引 20 条 [示意]
字段结构 = 第 4.2 节 match；同索引内 app_name/dataset_name/dataset_key 相同。
索引 1（app_name=xmage, dataset_name=xmage_DB_milvus）：
id | score | filename        | folder   | shard
1 | 0.3741 | IMG_100001.jpg | 春季上新 | shard_0.db
2 | 0.3620 | IMG_100002.jpg | 春季上新 | shard_0.db
3 | 0.3517 | IMG_100017.jpg | 春季上新 | shard_1.db
4 | 0.3402 | IMG_100023.jpg | 日常穿搭 | shard_0.db
5 | 0.3315 | IMG_100031.jpg | 日常穿搭 | shard_1.db
6 | 0.3198 | IMG_100045.jpg | 日常穿搭 | shard_0.db
7 | 0.3084 | IMG_100052.jpg | 配饰鞋包 | shard_1.db
8 | 0.2977 | IMG_100058.jpg | 配饰鞋包 | shard_0.db
9 | 0.2861 | IMG_100063.jpg | 配饰鞋包 | shard_1.db
10 | 0.2750 | IMG_100070.jpg | 家居生活 | shard_0.db
11 | 0.2634 | IMG_100081.jpg | 家居生活 | shard_1.db
12 | 0.2522 | IMG_100090.jpg | 家居生活 | shard_0.db
13 | 0.2410 | IMG_100104.jpg | 数码电器 | shard_1.db
14 | 0.2305 | IMG_100112.jpg | 数码电器 | shard_0.db
15 | 0.2198 | IMG_100125.jpg | 数码电器 | shard_1.db
16 | 0.2087 | IMG_100133.jpg | 美妆个护 | shard_0.db
17 | 0.1976 | IMG_100141.jpg | 美妆个护 | shard_1.db
18 | 0.1865 | IMG_100150.jpg | 美妆个护 | shard_0.db
19 | 0.1750 | IMG_100164.jpg | 食品饮料 | shard_1.db
20 | 0.1632 | IMG_100171.jpg | 食品饮料 | shard_0.db
索引 2（app_name=demos, dataset_name=demos_DB_milvus）：
id | score | filename       | folder | shard
1 | 0.3512 | DEMO_0001.png | 场景 A  | shard_0.db
2 | 0.3402 | DEMO_0005.png | 场景 A  | shard_0.db
3 | 0.3288 | DEMO_0012.png | 场景 A  | shard_0.db
4 | 0.3176 | DEMO_0018.png | 场景 B  | shard_0.db
5 | 0.3061 | DEMO_0023.png | 场景 B  | shard_0.db
6 | 0.2954 | DEMO_0031.png | 场景 B  | shard_0.db
7 | 0.2843 | DEMO_0037.png | 场景 C  | shard_0.db
8 | 0.2732 | DEMO_0044.png | 场景 C  | shard_0.db
9 | 0.2619 | DEMO_0050.png | 场景 C  | shard_0.db
10 | 0.2507 | DEMO_0058.png | 物料图 | shard_0.db
11 | 0.2398 | DEMO_0063.png | 物料图 | shard_0.db
12 | 0.2286 | DEMO_0071.png | 物料图 | shard_0.db
13 | 0.2175 | DEMO_0079.png | 海报   | shard_0.db
14 | 0.2064 | DEMO_0085.png | 海报   | shard_0.db
15 | 0.1953 | DEMO_0092.png | 海报   | shard_0.db
16 | 0.1842 | DEMO_0100.png | banner | shard_0.db
17 | 0.1731 | DEMO_0106.png | banner | shard_0.db
18 | 0.1620 | DEMO_0113.png | banner | shard_0.db
19 | 0.1512 | DEMO_0121.png | icon   | shard_0.db
20 | 0.1405 | DEMO_0128.png | icon   | shard_0.db
5.3 查询样例 × 10 [示意]（参数名与默认值均对齐代码）

# | 类型 | 请求要点                                          | 预期结果 [虚构]

1 | 文本 | query = 红色连衣裙                                  | 常驻数据集检索，25 条 / 页
2 | 文本 | query = 白色运动鞋，folders = 配饰鞋包                | 仅该文件夹内命中
3 | 文本 | query = 户外露营，shard_paths=shard_0.db,shard_1.db | 指定 2 个切片
4 | 文本 | query = 卡通猫咪，max_results=100, group_size=10    | 每页 10 条
5 | 文本 | query = 商务西装，page=2                            | 第 2 页，served_from_cache=true
6 | 文本 | query = 咖啡杯，min_similarity=0.2                  | 阈值 0.2 过滤
7 | 图片 | image_file=001.jpg                                | 以图搜图，阈值默认 0.2
8 | 图片 | image_file=002.jpg, folders = 食品饮料              | 图搜 + 文件夹过滤
9 | 文本 | query = 节日促销海报，wait_seconds=60               | 冷数据集同步等待加载
10| 文本 | query = 羽绒服，root_path_str=/DATA/.../demos/DB/DB_milvus | root_path_str 别名指定数据集
5.4 占位图
[代码中不存在占位图 / 静态图片资源]（服务不提供图片内容，仅返回 web_path）
5.5 异常样例（响应结构对齐代码真实定义）
202 搜索遇加载中 : {"success": false, "status": "loading",
"message": "数据集正在后台加载", "dataset": {...}, "retry_after": 5}
202 mount 后台   : {"success": true, "status": "loading",
"dataset": {...}, "retry_after": 5}
404              : {"detail": "路径不存在: /DATA/xxx"}
413              : {"detail": "图片过大"}
422              : {"detail": "query 不能为空"} / {"detail": "上传图片为空"}
/ {"detail": "无法处理图片: ..."}
503 繁忙         : {"success": false, "message": "服务繁忙，请稍后重试"}
504              : {"detail": "检索超时"}
500              : {"detail": "< 异常信息 >"}

六、接口样例

---

6.1 接口清单（默认 0.0.0.0:4444；--port/--host 可改）
方法 | 路径                | 说明                | 鉴权
POST | /search-by-text     | 文本检索（表单）     | 无
POST | /search-by-upload   | 图片检索（上传）     | 无
GET  | /folders            | 列举文件夹          | 无
POST | /datasets/mount     | 挂载数据集          | 无
POST | /datasets/unmount   | 卸载数据集          | X-Admin-Token*
GET  | /datasets           | 列出常驻 / 已加载数据集 | 无
GET  | /health             | 健康检查            | 无
GET  | /cache/clear        | 清空全部缓存        | X-Admin-Token*
GET  | /connections/clear  | 清理空闲数据集连接   | X-Admin-Token*

- 仅当设置 env ADMIN_TOKEN 时校验请求头 X-Admin-Token，未设置则全开放；不符返回 403
6.2 Embedding 请求 / 响应示例
[代码中不存在独立 Embedding 接口]
服务内部编码示意：输入 query 文本或图片字节 → 输出 L2 归一化 float 向量
[0.0123, -0.0456, 0.0789, ...]（维度由模型决定，代码未声明）
6.3 向量检索请求示例（POST /search-by-text，multipart/form-data）[示意]
query = 红色连衣裙模特图
project_name=xmage
dataset_path=/DATA/f60055380/clip_vit/milvus_db/xmage/DB/DB_milvus
shard_paths=shard_0.db,shard_1.db
folders = 春季上新
max_results=100
min_similarity=0.1
group_size=25
page=1
wait_seconds=40
参数说明：dataset_path /root_path/root_path_str 为同一参数的三个别名（按此优先级取值）；
全部参数均可省略走默认值（不传数据集路径时检索常驻数据集）
6.4 检索响应示例・第 1 页 [示意]
{
"success": true,
"query_type": "text",
"dataset_mode": "custom",
"datasets": [ { ... 同 5.1 索引 1 结构... } ],
"folder_filter": ["春季上新"],
"total_matches_found": 87,
"max_results": 100,
"min_similarity": 0.1,
"group_size": 25,
"total_groups": 4,
"current_page": 1,
"has_next": true,
"has_previous": false,
"matches": [
{
"id": 1,
"similarity_score": 0.3741,
"filename": "IMG_100001.jpg",
"full_path": "/DATA/f60055380/clip_vit/milvus_db/xmage/images/IMG_100001.jpg",
"web_path": "/local_images/xmage_DB_milvus/f60055380/clip_vit/milvus_db/xmage/images/IMG_100001.jpg",
"folder": "春季上新",
"app_name": "xmage",
"dataset_name": "xmage_DB_milvus",
"dataset_key": "/DATA/f60055380/clip_vit/milvus_db/xmage/DB/DB_milvus::9f8e7d6c5b4a::1698451200000000000:62914560000",
"shard": "shard_0.db"
},
{ "id": 2, "...": "（本页共 25 条，结构同上）" }
],
"model": "chinese-clip-vit-huge-patch14",
"served_from_cache": false,
"search_scope": "指定数据集",
"message": "搜索成功",
"project_name": "xmage"
}
6.5 检索响应示例・第 2 页（page=2，其余请求参数同 6.3）[示意]
与第 1 页仅以下字段不同：
"current_page": 2,
"has_previous": true,
"has_next": true,          （若仍有后续页；最后一页为 false）
"matches": [ { "id": 26, ... }, ... ],   （第 26~50 条）
"served_from_cache": true  （翻页命中 600s 服务端缓存）
6.6 权限校验接口
[代码中不存在独立权限校验接口]
仅管理端点校验 X-Admin-Token（见 6.1 注 *），不符返回 403 {"detail": "无权限"}
6.7 素材映射 / 素材读取接口
[代码中不存在]
（素材映射即检索响应 matches []；素材读取依赖外部按 web_prefix 部署的 Web 服务）
6.8 错误码清单（代码显式定义；未注明者响应体为 FastAPI 默认 {"detail": "..."}）
202 | 数据集加载中 / 等待超时 / 加载失败转后台           | 自定义体，见 5.5
403 | 管理接口 X-Admin-Token 不匹配                  | {"detail": "无权限"}
404 | 路径不存在 / 不在白名单 / 切片不存在 / 非 .db/ 无 .db | {"detail": "< 具体原因 >"}
409 | 卸载目标不存在、常驻或正在使用                  | {"detail": "数据集不存在、常驻或正在使用"}
413 | 上传图片 > 20MB                                | {"detail": "图片过大"}
422 | query 为空                                     | {"detail": "query 不能为空"}
422 | 上传图片为空                                   | {"detail": "上传图片为空"}
422 | 图片解码失败                                   | {"detail": "无法处理图片: ..."}
422 | mount 未传 dataset_path/root_path/root_path_str| {"detail": "dataset_path/root_path 不能为空"}
503 | 未指定数据集且无常驻数据集                      | {"detail": "常驻数据集未配置或未加载"}
503 | 全局并发 inflight ≥ 128                        | {"success": false, "message": "服务繁忙，请稍后重试"}
504 | 检索超时（SEARCH_TIMEOUT=90s）                 | {"detail": "检索超时"}
500 | 其他未捕获异常                                 | {"detail": "< 异常信息 >"}
[框架默认] Form/Query 参数缺失或类型不符时由 FastAPI 返回 422 校验响应（代码未自定义）
================================================================================
