# 缓存中的 available 时钟必须按时间语义比较，不能因字符串容器差异推翻冻结特征

- **问题**：V20已冻结713条结构上下文后，收益账本连接被拦下：旧账本`known_5m_available`为字符串，原请求读取器将同值转成UTC Timestamp；首项打印完全相同，却比较失败。
- **死胡同**：重跑或重选信号会把接口错误变成实验自由度；放宽全部比较又可能掩盖真实错钟。
- **有效路径**：只把`_available`补进时间字段识别，仍逐点精确比较，新增字符串/Timestamp双向CSV roundtrip和1ns变异拒绝测试。保留原失败与首次访问记录，从原四张哈希冻结表恢复会计，恢复路径禁止再读行情或重算特征。
- **通用规则**：先区分值不同、时间语义不同、序列化容器不同。技术重试继承原实验人口、规则与上下文哈希，不应重置成“第一次试验”。
- **牵连**：`hourly_impulse_structure_accounting._check_context`、`hourly_impulse_structure_research.resume_frozen_accounting`；20bp、72h、原V18退出和全251/462分母不变。
