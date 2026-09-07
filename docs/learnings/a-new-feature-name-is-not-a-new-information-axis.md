# 新特征名不等于新的信息轴

- **问题**：旧方向量“共振”失败后，要判断 DeltaPulse 是否值得成为一个不同的前置条件。
- **死胡同**：只按指标名字、普通放量或 buy/sell 文案判断独立性；旧代码的 obv_slope12_norm 名字带 slope，实际是12窗净量比例水平，而源码 buy/sell 也只是整根阳阴线volume分配。
- **有效路径**：并排比较输入、变换与可用时钟。旧12/48滚动净量水平，和本次EMA20净量比例再EMA5的前一小时变化，公式/时序确有区别；连续币价open近似前close，因此符号定义本身不构成强独立轴。固定K1前T−1/T−2，避免K1自身放量自证。
- **通用规则**：推荐新共振前，先做“原始字段→变换→时间→决策门”对照。先于当前bar成立叫预先改善，不等于首次启动；OHLCV代理不能称真实订单流。是否有经济增益仍需另验。
- **牵连**：V31 PROJECT_PLAN/config；ChartPrime lfaZVLub.pine；旧 scripts/research_btcusdtp_15m_multifactor_confluence.py 的方向量/OBV特征。此笔记只记录设计辨析，不宣称V31有效。
