# 持久清单真实合成联测

- A / codex/integration / var/worktrees/integration；基线 084c65b80d8cae52fc096604b4adf3bfb3724a36；修改 tools/manifest_cleaning_smoke.py、tests/test_manifest_smoke.py、adapters/dj_cleaning.py（独立 DSH_HOME）、本任务卡与 integration 交接。
- 依据 v1.9 14.6、A26/A28：使用四张程序生成 PNG（16/64/128/64 宽），模拟检索与媒体权限，真实 DJ 固定宽度规则，预期 pass=3/fail=1/unknown=0。不把重复内容算成有效交付，不调用旧 counting 的空路径输入。
- 本轮可受控运行既有 DJ Python 与 vendored 源码（只读使用、不安装/升级/修改），子进程环境白名单、离线设置、独立唯一临时 workspace、HF/DSH 缓存与 DSH_HOME；不启动其他窗口或共享可变 DJ workspace。
- 验收：SQLite 需求/检索/候选清单→清洗单独确认→真实逐条结果；临时下载删除；持久操作无临时路径；显式重新提交同 operation（新下载目录）仍同 DJ run；完成后重建仓库读取/幂等确认不重复发布。报告包含模式与阶段，不用 fake 降级冒充真实通过。
- 超时/失败保存可定位证据，不覆盖已有运行。禁止内网、真实业务数据、安装依赖、推送。真实失败时只修本范围，不改 vendored DJ。
