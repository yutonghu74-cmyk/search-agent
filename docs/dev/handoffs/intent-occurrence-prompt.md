# 意图引文 occurrence 提示收紧

- 主责 / 分支：A integration / `codex/intent-occurrence-prompt`；独立 worktree `var/worktrees/intent-occurrence-prompt`。
- 可信基线：`40f41cdb5e9f635aff846f02bc168064c58f337b`；source_base `02b27deeb7f258a67044859db7b7286fac951e53`。
- 状态：READY_FOR_REVIEW。
- 原因：真实 `gpt-4o-mini` 合成诉求把第二条唯一引文的 `occurrence` 写成字符位置 10，后端按严格契约返回 `MODEL_GROUNDING_FAILED`。收紧提示后同一模型返回 0；代码只改提示，不放宽校验。
- 验证：首次定向门禁被协作检查器正确拒绝为高风险；A 在主线发布完整测试任务卡 `c9cda1c` 后，`python3 tools/collab_gate.py package --base c9cda1cfcabb4e6552fd5b5efa75229d6d355b7a --task docs/dev/tasks/intent-occurrence-prompt.json`：628 项 PASS、`LOCAL GATE PASS`、退出码 0。
- 真实再试：隔离 8086、真实 `gpt-4o-mini`，合成诉求“找3张狗图片，不要文字”返回 `LIVE_MODEL_INTERPRETATION`、`media_type=image`、原文引文匹配；`execution_allowed=false`。模型把标点列为未解析且能力未注册，当前仍不可自动执行。真实模型长期稳定性、业务质量与生产权限联测 NOT_RUN。
