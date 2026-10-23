# 逻辑查询分支预览

依据 v1.9 §5.3、§12.4。本切片只编译逻辑提案，不创建另一套物理 RetrievalPlan、不替代已有批准账本。

`GET /v1/retrieval/tasks/{task_id}/query-branches` 从当前已确认 revision 读取 QuerySpec，复用任务发起人和项目读取权限；返回前再次校验 revision、task_version、generation，变化返回 409。无写操作，不创建 run、批准或查询执行记录。响应包含三项版本绑定、proposal_digest 和 preview。

preview 的 schema_version 为 query-branches@1，原文单独保留；第一条分支始终为 original，随后为各主标签 primary 和辅助词 expansion。辅助词不含主标签前缀时，以“主标签 空格 辅助词”组合；不推断同义词、场景/属性类别或词权重。只归一连续空白，不改变大小写、标点、产品编号或否定。

每条分支包含 query_text、query_role、source_term、skill_version、intended_libraries、constraint_refs、query_fingerprint、branch_id、origins。skill_version 是用户输入或现有词来源版本标识，不声称调用了真实 Skill。指纹绑定编译器版本、文本、媒体类型及结构化规则/来源限制。相同指纹只出现一次，origins 保留所有不同来源，包括跨组来源；首个来源确定顶层角色。query_spec_digest 绑定整个输入，preview_digest 绑定整个预览。

规则引用为 rule:<rule_id>，来源限制为 source_constraints:<index>，同时携带原始结构化约束的独立副本。原文中的否定仍保留，但本模块不解析成清洗规则，不能宣称否定已执行。

status=preview_only、executable=false、library_resolution=unresolved、intended_libraries=[]。不把 refill 的已批准库列表当作当前选库结果；未接入授权目录、编码器、模型、查询执行或融合排序。未来物理计划必须经现有后端批准与版本栅栏方可执行。
