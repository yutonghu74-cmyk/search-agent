# 开放需求理解审阅契约 v1

状态：本地纯校验切片；不替代 QuerySpec@2，不自动理解、不创建任务或批准执行。

## 数据流

原文 + 可选的候选解释 → 严格结构/原文绑定校验 → 自动补未覆盖文本 → 后端能力检查 → 用户审阅报告。

调用 `IntentReviewService.review(raw_request, proposal=None)`。身份、权限、草稿持久化和确认事务不在此纯函数层完成；不得把其返回值直接作为 create_task 请求或执行许可。

候选解释字段固定：

- schema_version：intent-interpretation@1。
- raw_digest：`digest({"raw_request": 原文})`，使用已有规范化 SHA-256 实现。
- media_type：image/video/text/null。null 需要澄清，不猜目标类型。
- items：最多 100 条；每条严格包含 item_id、start、end、quote、kind、hardness、predicate_ref、operator、value、question。
- start/end：Python Unicode 字符位置的半开区间，不是 UTF-8 字节或 JavaScript UTF-16 偏移；quote 必须严格等于该片段。原文最多 16000 字符。
- kind：intent/constraint/preference/source/quantity/dedup/unresolved；hardness：hard/soft/unspecified。constraint 必须 hard，preference 必须 soft。不得把“横图优先”偷偷转成禁止竖图。
- predicate_ref/operator/question 可为 null；value 只接受有限 JSON 标量。结构化来源、区间、配额需后续版本扩展，当前不能擅自解释为已支持。

示例语义：人工或模型提出“不要文字”→constraint/hard/text.present@1/eq/false；“横图优先”→preference/soft/orientation@1/eq/landscape；“沙发完整”标准未定→question。它们只是候选解释，不代表已有 OCR、完整性模型或排序执行器。

未覆盖的连续非空白字符自动生成 unresolved 条目，保留其原文并阻断自动执行。标点也保守保留，解释提供者可将标点包含在对应片段中。允许同一片段支持多条解释；字符覆盖不证明语义完整或正确，不能据此宣布需求理解通过。

## 能力检查

Capability 由可信后端配置，模型不能提交审批/验证器/脚本。匹配 kind + predicate_ref + media_type，再核对 operator 和值类型；verified/manual 要求验证器和评测/证据引用。当前仅检查配置完整性，不自动验证证据文件真实性、许可证、模型可用性或生产质量；部署负责人须审查后配置。

默认目录为空：QuerySpec 里注册过名称，不代表已有验证能力。测试中的 fixture-validator/fixture-evaluation 仅为模拟数据。固定 image-width@1 的 smoke 不等于开放规则能力发布。

输出 capability_status：automatable/needs_clarification/unsupported/manual_review；其中 automatable 只说明候选映射匹配受信配置，不是素材判定 pass。未支持偏好作为可见警告；未支持硬要求、待澄清、人工证据路径均阻断自动执行。直接同 kind/predicate 的相反 eq 硬约束标 CONFLICTING_EQUALITIES；不宣称能识别所有语义矛盾。

报告固定 requires_user_review=true、execution_allowed=false、creates_task=false，并含 registry_version/digest、review_digest。无模型时全文未解析；任何成功响应都不能用于跳过确认或生成业务标识。

## 本地体验

```sh
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 tools/intent_review.py --request '找客厅图，不要水印，横图优先'
```

此命令不调用模型，会如实显示全文待澄清。`--interpretation <候选JSON>` 可查看人工/外部提供的解释经校验后的结果，默认仍无已验证能力。CLI 不保存文件、不联网；向终端打印的原文应自行遵循数据保密要求。

## 显式真实模型入口

`IntentModelClient(load_dsh_config(profile_path, credential_path, yaml_python=existing_yaml_python)).interpret(raw_request)`
复用 DSH 默认 provider/model 与对应 apiKeyEnv 凭据引用，支持当前 refs 格式及旧平面映射；不修改 DSH 配置，不读取其他服务凭据用于请求。配置必须为 HTTPS openai-completions；凭据文件不得开放组/其他用户权限。

命令 `PYTHONPATH=src python3 tools/intent_model.py --run-live --profile <本地profile路径> --credentials <私有凭据路径> --yaml-python <已安装YAML的解释器路径> --request <可外发需求>` 显式联网，输出到终端，不保存原文/凭据。不要把机密业务内容放入命令行。需要可用的既有 YAML 运行时，不自动安装依赖。

请求仅含系统提示和用户原文；凭据仅在认证头。禁重定向、禁自动重试；网络超时上限 60 秒（urllib 套接字操作超时，并非整体截止时间），响应上限 1 MB。上游错误脱敏。模型只提供原文 quote/occurrence，后端定位字符坐标并做已有校验；此预览不创建正式任务或派发清洗。

Chat `intent-preview` 若收到模型改写或伪造的 quote/occurrence，或模型返回不在 `image/video/text/null` 内的媒体类型，服务端绝不修补或接受该解释，而是整体丢弃模型条目，返回 `mode=SAFE_CLARIFICATION`：唯一解释项覆盖完整用户原文，`kind=unresolved`、`media_type=null`、`execution_allowed=false`。调用方只能留在澄清态：用户仅回复“是/图片”时，把图片类型作为补充重新分析；用户完整重述时替换旧诉求。无论哪种，必须等新的 review 明确 `media_type=image` 且主体、数量齐备后才可扩词或提单，不得从失败模型结果补全，也不能因反复回复“是”循环自动推进。其他模型错误仍按原失败关闭。后续可另行迁移为服务端先生成片段 ID、模型仅引用 ID 的协议；本兼容回退不等于该协议已实现。

2026-10-22：现有 qwen3.7-plus 的一条合成需求真实调用成功，数量、文字/水印否定、横图软偏好和完整性澄清可见；此前一次连接失败，不把本次成功视为稳定性验证。默认能力目录仍为空，不能执行。模型未覆盖的功能词/标点仍保守列入待澄清，用户体验需后续改进，不静默忽略。

后续：业务理解评测、持久草稿版本/澄清修订、确认后转换到执行规格、完整能力参数/证据 schema 和规则编译器。未实现前不能称“开放需求自动执行”。

## 解释草稿与多规则编译预览切片

`IntentDraftStore` 使用单独的私有 SQLite 文件持久化原文和解释候选。调用方须注入项目访问检查器；创建、读取和修订均要求租户/创建人及当前项目权限。创建与修订使用请求键幂等，修订还要求 expected_version；重放返回原操作版本和当前版本，内容变化报冲突。每次读取依据当前受信能力目录重新审阅，因此能力撤回可立即阻断后续预览。数据库文件仅允许所有者访问；本地单库试验不等于内网业务账本验收。这个草稿没有 task/batch/revision/run，也不能调用既有正式任务的确认接口。

`IntentCompiler.preview(actor, draft_id, expected_version, expected_review_digest, action="confirm_preview")` 只接受当前版本及用户明确审阅过的摘要。硬条件全部必须由当前能力目录判为 automatable，并有匹配的受信 DetectorBinding；现在只接受 `eq` 运算。相同后端检测步骤复用，独立硬规则逐条保留；软偏好单列，不变成硬过滤。缺失/矛盾/未支持/人工/主观澄清都阻断。默认能力目录和检测器绑定为空；测试中的 fixture 引用不代表产品已发布这些检测器。输出是稳定摘要的编译预览，`execution_allowed=false`，不构造 CleaningProposal、不发作业。

`evaluate_facts` 是给可信检测事实使用的纯函数，规则 verdict 为 pass/fail/unknown，整体为任一 fail 则 fail、否则任一 unknown 则 unknown、全部 pass 才 pass。此结果不等于有效去重素材数；函数不采集媒体、验证外部事实来源、执行 DJ 或写业务账本。真正多规则 Worker 接入仍需发布算子清单、事实协议、资源/失败语义、任务版本绑定和真实合成数据验收。
