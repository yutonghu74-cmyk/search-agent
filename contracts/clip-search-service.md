# 内网 CLIP 组合检索服务适配契约

## 当前边界

内网 CLIP 服务在 `POST /search-by-text` 内部完成文本编码和 Milvus 检索，不提供独立
embedding 接口。因此它不能直接冒充现有的 `EmbeddingProvider` 与
`VectorSearchGateway` 两个分离端口，也不能用虚构向量接入现有执行器。

当前实现 `CLIPTextSearchClient` 只完成契约级模拟接入：

- 物理目标、模型、数据集、切片和文件夹过滤模板来自受信配置；服务地址只从环境变量读取。
- 请求固定为有界 `multipart/form-data`，支持文本、指定数据集、最多四个切片、文件夹模板、
  结果上限、阈值、页大小、页码及有限等待时间。
- 每次调用前后重新校验库访问权；未知 binding、任意过滤表达式、失效权限均在结果发布前拒绝。
- 分页游标由业务侧签名，绑定租户、用户、任务、授权版本、计划、查询、binding、过滤模板和页大小。
- 后续页的数据集 `key + version` 必须与第一页相同；变化时返回
  `INDEX_SNAPSHOT_EXPIRED`，不得静默从第一页重查。
- 上游只有 600 秒结果缓存，没有不可变读取快照。因此结果标为 `live`；最后一页记为
  `depth_limit`，不宣称整个内网索引已经权威耗尽。
- 命中使用 binding、索引版本、dataset key、shard 和 full path 生成业务侧不透明 ID，
  同时保留 filename、path、web path、folder、dataset 和 shard 来源字段。
- 可选 `clip-media-mappings@1` 受信配置把精确的 binding/index/dataset version/shard/full path
  映射到统一 `MediaRecord`。只有映射项包含稳定素材版本、受控读取引用和 SHA-256 内容摘要时，
  候选才返回稳定 `candidate_id`；路径、数据集版本或索引变化均为 `mapping_missing`，不做模糊猜测。
  页面不返回受控读取引用和源素材主键，映射成功也不代表清洗通过或已有预览授权。

## 持久化后台执行器

已新增独立的 `SQLiteCLIPSearchJobs` 和 `CLIPSearchExecutor` 执行核心，用于避免把组合式请求
塞入要求真实 `QueryVector` 的旧账本：

- 作业身份绑定 task、revision、generation、task version、需求摘要、授权版本和全部查询成员。
- 每个成员分别保存游标、页数、尝试次数、错误和完整来源字段；网络调用前持久化尝试，
  进程重启后从同一页恢复。
- 单页最多重试三次；不可重试错误或达到上限后进入失败状态，不把失败当成空结果。
- 网络返回后再次读取任务 scope；新版本已产生时把旧作业标为 `superseded`，晚到结果不写入。
- 每个作业不可变保存，旧版本只保留审计，不覆盖新版本候选。

组合式模式现已可选装配到现有 first-search API：词表确认后，对每个查询纳入当前已配置、
已授权且能力匹配的预制 binding，同一查询可以使用多个来源；总成员超过 64 时明确停止，
不静默截断。系统自动创建作业并交给后台线程；进程启动会重新调度 SQLite 中的 queued/running 作业。现有
`/first-search/progress` 和 `/first-search/candidates` 返回作业状态、原始命中及来源字段，页面可
沿用检索节点轮询。此模式通过独立 CLI 配置启用，与 `LOCAL_EXACT` 夹具互斥。

任务确认与检索作业创建之间使用任务 SQLite 中的 `search_dispatch_outbox`：确认和待投事件在
同一事务提交，后台启动时恢复未投事件，正常调用 `auto_start` 后才标记 delivered。事件绑定
revision、generation 与 task version；旧版本在投递前会标记 superseded，确定性事件 ID 和
检索幂等键避免重复确认或重复唤醒创建重复作业。这解决了本地单任务库到 first-search 的崩溃
窗口；生产多实例仍需要正式数据库、竞争租约和监控。

正式接入前还需要：

1. 用公司权威元数据接口替换本地映射 JSON，并联测稳定素材主键、版本和内容摘要变化语义。
2. 接入项目/素材用途授权。上游搜索接口本身无鉴权，网络可达不等于业务有权访问。
3. 接入受控素材读取服务。`web_path` 不是授权媒体引用，不能直接交给清洗、预览或打包。
4. 实测模型输出维度、服务版本、缓存过期后的翻页行为及多切片数据变化语义。
5. 生产多实例补充投递租约、告警和费用对账；当前 SQLite outbox 是本地可靠恢复基线。

完成上述装配及真实内网联测前，只能声明模拟传输契约通过，不能声明真实向量库已接通。
