# A 集成任务：本地交互演示

- 角色 A，codex/integration，独立工作区 var/worktrees/integration；基线 bd395f2。
- 范围：src/dsh_retrieval/demo.py、tests/test_demo.py、README 与总控交接记录。复用既有服务，不更改业务计数、批准或执行契约。
- 用户已授权测试后的本地提交/合并；不推送、部署、使用真实数据或安装依赖。
- 显式选择 simulated/real 执行器；三次人工确认分别对应需求、候选、清洗。不提供自动批准开关。固定合成图片不代表语义检索。
- 验收：拒绝任一确认不启动执行器；完整模拟流程得到 3 pass / 1 fail / 2 唯一通过；失败、Ctrl-C、超时显示明确状态并先栅栏取消；报告标注模拟/真实，accepted_count=null。
- 独立工作区只跑模拟单测；合并后在主工作区独立运行目录使用已有 DJ 环境测试真实交互。不共享可变环境，不修改角色检查器。A 共享集成以回归和记录验收，不冒充 core 范围门禁。
