# 切换模型服务商必须同时迁移凭据和能力契约

- **问题**：视觉工作台从 Gemini 切换到智谱，旧设置、页面、图片限制和输出结构都绑定了原服务商。
- **死胡同**：只换 API 地址会误用旧 Key、拒绝智谱含点号的 Key，或向视觉模型发送只在文本模型文档中定义的参数；通用 `thinking` 配置也不能覆盖具体型号仅支持开启思考的约束。
- **有效路径**：固定官方目标域名，配置明确记录 provider，跨服务商不继承凭据；按专属视觉文档配置图数、单图大小和思考参数，以 schema prompt 加本地严格验证接收结果。先用小请求验证模型和认证，再用真实候选及完整参考集核验图片路径。
- **通用规则**：迁移服务商时逐一核对认证、模型输入、参数、错误码、响应解析、页面接收方及历史记录；模型名称相似或接口风格兼容并不代表能力契约相同。
- **牵连**：`yoyo/vision_research/zhipu.py`、`server.py`、`settings.py`、`schemas.py`、`images.py` 与前端；[官方对话 API](https://docs.bigmodel.cn/api-reference/模型-api/对话补全)、[GLM-5.3-Flash](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash)。
