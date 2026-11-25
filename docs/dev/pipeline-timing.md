# 对话到候选结果的分段计时

本片只增加测量，不改变批准、授权、计数、重试或调度规则，也不声称已经优化实际模型/向量查询速度。

## 记录位置

服务输出结构化 `pipeline_timing` JSON 日志；沿用现有服务日志目的地，不新增业务表或保存对话内容。
每次 HTTP 请求/后台激活有独立的性能 `trace_id`，使用 `task_id / revision / generation / task_version` 或 `job_id` 关联。
复制请求上下文的异步线程创建独立记录并保留 `parent_trace_id`，不继续修改已结束请求的计数。
扩词作业从 `_run_batches` 开始测量，ThreadPool 排队时间不在该激活时长内；原公开 `timing_ms.first_batch/complete` 仍包含从入队起的墙钟等待。
已有对话命令 `queue_wait_ms` 保持独立，不能把它重复加进原处理耗时。

| 阶段 | 记录 | 含义 |
| --- | --- | --- |
| 需求理解 | requirement_understanding | 一轮模型需求理解，含校验/可能的格式修复 |
| 模型调用 | model_request；原 model_initial/model_repair | 模型请求、读取、解析及已有有界重试；修复另行计数 |
| 扩词 | expansion_job / keyword_expansion | 一次后台激活和其中每批生成；首批墙钟时间继续见作业 timing_ms |
| 选库/编码 | search_preparation / query_preparation / query_encoding | 准备各批准分支及文本或参考图编码 |
| 检索 | search_activation / retrieval_tick / vector_search | 激活/调度和实际网关调用，vector_attempts、retrieval_retries区分尝试和待重试 |
| 持久化 | retrieval_commit | 校验并提交已返回页，不与网关调用混算 |
| 候选 | candidate_validation / candidate_resolution | 初检清单构建、媒体映射与版本/权限校验，不等同于VLM清洗 |
| 状态读取 | workspace_projection / retrieval_progress_read | 工作区组装和其检索进度读取子阶段，属于包含关系 |
| 响应 | response_encode；HTTP total_ms | JSON编码；请求处理和响应写出，HTTP状态另记 |
| 浏览器 | http：headers_ms/body_ms/server_ms | 发起到响应头、响应体读取+JSON解析、响应头提供的本次服务处理时间 |
| 页面 | progress_render/results_render/candidate_gallery_render（兼容旧列表candidate_render） | 同步DOM更新；对应 *_frame_wait 是到后续绘制机会的等待，并非浏览器实际绘制或图片解码时间 |

所有阶段时间均为单调时钟耗时。嵌套阶段是包含关系，不可把阶段相加或把跨激活总时长相加作为端到端耗时。
HTTP响应头 `Server-Timing: pipeline;dur=...` 截止发送响应头前；最终日志的 total_ms还包含写出响应体。
浏览器 headers_ms 包含服务处理和传输，不能称作纯网络延迟，也不要把它与server_ms重复相加。

## 定位一次等待

1. 在浏览器网络面板读取 `X-Performance-Trace-Id`，用相同值查服务日志。
2. `started` 后没有对应 `finished/failed` 表明该阶段尚未结束或进程在完成前退出，不能当作0毫秒。
3. `completed` 给出本次激活的阶段累计、调用数、失败数。检索待重试时调度函数本身可能成功返回；应同时查看 vector_search.failures 和 retrieval_retries，不能当作检索完成。
4. 后台扩词按公开 job_id 查；检索按 task_id及revision/generation查。正式任务创建前没有task_id，不伪造任务或跨用户关联。
5. 幂等回放响应体保留原处理timings/trace_id；性能响应头记录本次HTTP读取，回放不重发原conversation耗时。

浏览器控制台执行 `window.dshPipelineTimingRecords()` 获取当前标签页最近200条数值记录；HTTP记录和可关联的渲染记录带同一trace_id。
已有 `window.dshChatTimingSummary()` 保留原行为。记录存在sessionStorage，不跨标签页/进程持久保存；不要把用户确认期间的停留混入服务器处理时间。

服务日志汇总示例（路径用当前实际日志替换）：

```sh
python3.12 tools/report_pipeline_timings.py /path/to/service.log --task-id rt_example
python3.12 tools/report_pipeline_timings.py /path/to/service.log --trace-id 0123456789abcdef0123456789abcdef
python3.12 tools/report_pipeline_timings.py /path/to/service.log --job-id expjob_example
```

工具只读数值完成记录，忽略其他日志正文，最多保留最近10000条匹配完成记录；按operation.stage输出样本数、调用数、失败数和样本p50/p95。
阶段累计是“每次激活的该阶段总耗时”，不是单次网关请求延迟分布；没有完成记录时groups为空，不伪造零延迟。
测试夹具结果不能当作真实模型、Milvus或现场P95。需要用户后续真实操作样本才能判断下一步优化哪个阶段。

## 边界

不记录提示词、回复、媒体地址、向量、请求体、模型端点或凭据；失败也不写异常正文。
记录失败不改变业务返回/异常，不新增任务或触发后台业务重试。现有服务日志保留策略仍适用，stage事件在阶段结束前后输出。
本片不包括清洗执行/打包全链路埋点，也没有宣称连续跨人工确认的一条端到端trace已完成。
