# Reasoning must leave room for the final structured answer

- **问题**：GLM-5.3-Flash 的实测失败返回 `finish_reason=length`；一次请求的 8,192 completion tokens 中有 8,190 reasoning tokens，最终正文长度为 0，并非本地 JSON 解析器损坏。原始证据保留在 VLM API 记录 `0cfa1699f91b4c89a2a1d12de7c64214`。
- **死胡同**：相同 8,192 上限重复请求仍多次截断；补括号、接受可解析的截断对象或改读 reasoning 都不能证明模型完成了判定。降低 owner 选定的 max 推理也会改变研究条件。
- **有效路径**：先检查真实请求预算、完成状态和分项用量，再仅把 GLM-5.3 家族识别预算提高到 32,768，保留规则、推理档位、单次调用及失败原文。官方最大为 131,072，见 [核心参数](https://docs.bigmodel.cn/cn/guide/start/concept-param)。32,768 是本地工程预算，不是模型极限，也不是永不截断的保证。
- **通用规则**：结构化结果缺失时，先区分“根本没生成正文”“正文被截断”“完整正文不满足 schema”。思考用量属于生成预算的本次实测证据应独立保存；失败不可升级为有效识别。
- **牵连**：`yoyo/vision_research/zhipu.py` 的识别与三臂对照共用同一预算策略；`static/conversation.js` 展示嵌套 reasoning tokens；旧失败记录不改写，不自动重放旧图，也不更改其他模型或连接测试预算。
