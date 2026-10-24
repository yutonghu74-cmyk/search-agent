# 已批准 VLM 检测器切片

`VLMRequirementDetector` 只由受信服务构造：需求文本必须来自发起人已确认的规则复核，`source_review_digest` 必须等于 `CleaningRecipe.source_review_digest`。模型选择来自受信模型配置；构造时核对 `model_binding`，运行时 `ModelRuntime.assess` 再核对当前配置。公开草稿、素材 URL、模型输出都不能直接构造批准作业。

检测器固定发布 `image.semantic-match@1` 布尔事实。确认配方应使用 `eq true`；VLM `pass`→`true`、`fail`→`false`、`unknown`→无事实/无证据。模型/传输错误抛出 `ServiceError`，不产生可计数的清洗通过。只接受 PNG/JPEG 字节；授权读取、内容摘要校验、generation 栅栏和结果发布由现有 `run_approved_multi_rule` 链路负责。

发布摘要包含能力注册表、制品清单及逐检测器 `release_config_digest`。后者包含需求文本、模型绑定、提示版本、规则来源摘要和谓词；运行前重检漂移，旧批准摘要不能复用。此变动将发布摘要版本改为 `released-detector-registry@2`，旧批准须重新准备并由发起人确认。

本切片只提供受控适配器和发布栅栏。尚未把 Chat 分组/否定条件自动编译成该规则，也未建立真实授权图片网关、VLM 证据持久化或权威计数；模拟测试不代表真实业务端到端通过。
