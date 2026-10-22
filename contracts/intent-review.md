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

后续：受控模型适配、业务理解评测、持久草稿版本/澄清修订、确认后转换到执行规格、完整能力参数/证据 schema 和规则编译器。未实现前不能称“开放需求自动执行”。
