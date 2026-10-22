# 通用多规则清洗执行器任务卡

- 角色 cleaning；独立 worktree `/Users/hw-edit/Documents/ChatGPT/search-agent/var/worktrees/cleaning`；分支 `codex/cleaning-adapter`；基线 `2673bc02d65ab54c9e48acf6823d43b6f2e18dda`。
- 契约：`contracts/multi-rule-cleaning.md`、`src/dsh_retrieval/multi_rule_contracts.py`，不得修改共享契约。只改 `src/dsh_retrieval/adapters/cleaning/`、`tests/cleaning/`、`docs/dev/handoffs/cleaning.md`。
- 验收依据 v1.9 7.3、7.7、7.8 及 A09/A14：仅受信 ApprovedCleaningJob 可进入执行；调用方注入当前任务/版本/代次授权检查、授权媒体读取器和已登记检测器。每素材核验内容摘要，步骤只执行一次；多个硬规则分别判定，缺失/异常/证据不符为 unknown，旧版本或撤权不得发布。
- 用合成字节、合成检测器验证多规则 pass/fail/unknown、共用步骤、权限/代次变化、输入篡改、异常和证据缺失。不得把合成检测器或本地测试标为真实 DJ 多规则联测；不增加用户指定 Python 路径、下载地址、任意脚本或真实内网调用。
- 完整回归后运行 `python3 tools/collab_gate.py check --role cleaning --base 2673bc02d65ab54c9e48acf6823d43b6f2e18dda`；交接记录真实结果，再由 A 审查并本地集成。
