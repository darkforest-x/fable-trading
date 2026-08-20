# 研究祖先相同不代表候选事件语义相同

- **问题**：用户的 Pine 与项目都来自“均线密集启动”，直觉上容易认为现有 Local Signal V2 判断层可
  直接套用。实际 V9 是 SMA10/60 单点 crossover + EMA100/EMA200 方向 + oscillator，项目候选则是
  EMA8/13/21/34/55 与 EMA144/200 的 ribbon density。
- **死胡同**：根据策略名称、设计故事或共享的 `ma_spread_pct` 特征判断两套候选同分布；或者把旧
  LightGBM 的分数当通用“形态质量”。这忽略了候选生成、方向坐标、标签障碍与执行状态均不同。
- **有效路径**：用项目现有、未经调整的 strict/expanded masks 逐笔映射 V9 signal bar，并对每个时期
  穷举完整 signal path 的循环时间移位零假设。276 笔 V9 入场只有 4 笔（1.45%）满足 strict、29 笔
  （10.50%）满足 expanded；final strict 仅 1/110，三段 strict 均无显著富集。
- **通用规则**：复用模型前先做“候选语义重合审计”：同一时间戳、同一方向、同一因果可见面、同一标签
  和执行契约必须逐项成立。故事同源只能说明值得研究，不能授权复用数据集、阈值或模型。
- **牵连**：`scripts/analyze_pine_eth_15m_density_overlap.py`、`yoyo/data/indicators.py`、
  `yoyo/layers/l2_judgment/features.py`、旧 frozen LightGBM、`active_bundle.json` 缺失。
