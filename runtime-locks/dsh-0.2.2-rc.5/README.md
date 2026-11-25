# DSH 0.2.2-rc.5 运行发布

实际 Harness 固定为 npm `@deepseek-ai/dsh@0.2.2-rc.5`，使用 Node >=24.9。`package-lock.json` 锁定直接及传递依赖；不要用浮动 latest。测试使用已有 Node 24.22.3，未修改系统 Node。

Python `deepseek-harness-sdk==0.2.2rc2` 只作为控制客户端。其元数据要求同版 `deepseek-harness-runtime-bin`，但本发布**显式设置 dsh_bin 为 npm 0.2.2-rc.5 CLI**，不启动 wheel 自带运行时。两者版本分别记录，不能把 Python 客户端版本当成实际 Harness 版本。

## 独立安装

在新的运行目录复制本目录 package.json/package-lock.json，使用明确 Node 24 环境执行：

```sh
npm ci --prefix "$DSH_RC5_NPM_ROOT" --registry https://registry.npmjs.org --no-audit --no-fund
python3 -m venv "$DSH_RC5_CONTROLLER_ENV"
"$DSH_RC5_CONTROLLER_ENV/bin/python" -m pip install --index-url https://pypi.org/simple -r controller-requirements.lock
```

变量须指向独立目录。不要覆盖已确认任务使用的旧环境。明确指定 DSH_HOME 后运行 `$DSH_RC5_NPM_ROOT/node_modules/@deepseek-ai/dsh/lib/bin.js --version`，必须为 `0.2.2-rc.5`；PATH 中的 node 必须为选择的 Node 24。

## 装配

使用 config/harness-releases.example.json 填写独立 Python、短 HOME、npm CLI、Node 和已安装锁文件的绝对路径，然后调用 adapters.harness.composition.build_planning_runner。它只接受代码中注册的 SDK 发布清单，两个角色默认指向 rc.5。模型环境与语义绑定从后端不可变配置解析，配置内不放密钥。

版本 probe 同时核对 SDK/配套 carrier 元数据、npm 包版本和实际 CLI `--version`。缺少显式 CLI/Node/锁文件会失败；不会因路径不存在改为运行 wheel 的 rc2。legacy 0.2.1rc2 保留为显式兼容选项，不是自动回退。

已用真实旧/新 SDK、实际 rc.5 CLI、本机确定性模型接口验证两个角色及越权来源拒绝。相关证据见 docs/dev/reports/harness-rc5-compatibility.md。真实模型效果、生产预算出口、持久化恢复和现场切换仍需后续验证。
