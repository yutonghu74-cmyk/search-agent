# 受信文本编码适配（R03 局部）

`HTTPEmbeddingProvider` 实现现有 `EmbeddingProvider` 端口。它只按已登记的 `IndexBinding` 选择编码器，不接受 Chat 请求提供的 URL、模型或向量；每次调用前后检查当前库授权。返回模型、向量维度、有限数值和余弦非零范数必须与物理索引配置一致，否则失败关闭。不同索引即使维度相同也不会共用编码器。

私有部署配置示例（不要提交真实地址或密钥）：

```json
{
  "schema": "embedding-endpoints@1",
  "entries": [
    {
      "binding": {
        "binding_id": "dogs-v1",
        "library_id": "dogs",
        "index_version": "v1",
        "encoder_ref": "dog-encoder@1",
        "vector_dimension": 512,
        "metric": "COSINE"
      },
      "model_id": "private-embedding-model",
      "model_version": "v1",
      "preprocessing_version": "text-v1",
      "base_url": "https://embedding.example.invalid/v1",
      "api_key_env": "DSH_DOG_EMBEDDING_KEY"
    }
  ]
}
```

配置项须与 `PhysicalIndexRegistry` 的完整绑定、模型 ID、模型版本及预处理版本严格匹配。密钥只从命名的环境变量解析，端点必须是 HTTPS `/v1`；不跟随重定向。请求使用兼容 [Embeddings API](https://developers.openai.com/api/reference/resources/embeddings/methods/create) 的 `/embeddings` 单条文本与浮点数组响应；单次超时默认 10 秒、最多 30 秒，响应上限 1 MB。不自动重试，也不缓存；重复计费、缓存授权复验与请求预算须在接入生产协调器前另行完成。

这只是可注入的真实编码端口，尚未接入 8085 的 `LOCAL_EXACT` 检索、真实向量网关或生产启动装配。模拟传输测试通过不代表用户提供的服务支持 embedding，也不代表同模型的预处理已通过端到端校验。真实联测需验证供应端模型身份、预处理、维度、速率限制及相应物理索引向量空间。
