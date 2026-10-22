# 真实需求解析模型接入

- 用户本轮明确要求复用已加入账密，优先真实模型接入；暂停尚未开工的持久澄清任务。
- A / codex/integration / var/worktrees/integration；基线 22c62143dd20d49f0426f1fccd2232fd13de6699。范围 src/dsh_retrieval/intent_model.py、tools/intent_model.py、tests/test_intent_model.py、本任务卡、contracts/intent-review.md、integration 交接及生成证据。
- 依据 v1.9 5.1/5.2、7.8：模型只建议解释；后端校验原文与能力，不允许模型设置身份、执行状态或调用工具。原文未覆盖保留 unresolved。
- 只读解析用户指定 DSH profile 与私有凭据文件，选择默认 provider/model/apiKeyEnv；仅该 key 进入发往已配置 HTTPS endpoint 的 Authorization 头，不打印/持久化/放命令行，不转发其他凭据。不改 DSH 配置；禁重定向、禁自动重试、限制响应大小/超时。
- 模型返回 quote/occurrence，不信任其字符坐标；服务端从原文定位并产生 item_id/摘要，再送现有审阅层。多次出现的文本明确 occurrence，缺失或伪造片段拒绝。默认能力目录仍为空，不把语义解析成功等同于规则执行能力。
- 合成真实验收：数量/场景/文字水印排除/软偏好/模糊完整性；记录模型、结果及待澄清项，不记录网关地址和密钥。以实际输出检查否定与软偏好，不把结构测试数量当理解准确率。
- 使用既有安装了 YAML 读取库的解释器；不安装依赖、不运行 DJ、不接内网、不推送。只调用一条合成需求，若失败记录脱敏错误，不自动换服务商或模型。
