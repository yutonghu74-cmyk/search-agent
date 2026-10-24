# 意图引文 occurrence 提示收紧

- 主责 / 分支：A integration / `codex/intent-occurrence-prompt`；独立 worktree `var/worktrees/intent-occurrence-prompt`。
- 可信基线：`40f41cdb5e9f635aff846f02bc168064c58f337b`；source_base `02b27deeb7f258a67044859db7b7286fac951e53`。
- 状态：IN_PROGRESS。
- 原因：真实 `gpt-4o-mini` 合成诉求把第二条唯一引文的 `occurrence` 写成字符位置 10，后端按严格契约返回 `MODEL_GROUNDING_FAILED`。收紧提示后同一模型返回 0；代码只改提示，不放宽校验。
- 验证：待记录定向门禁及真实再试。真实模型质量、长期稳定性、生产权限联测 NOT_RUN。
