# 与原信号共线的入场门不是一个新条件

- **问题**：给 BB×Stoch v2 加 Parabolic RSI「超卖才做多、超买才做空」的门，直觉上是加了一层独立确认；实跑下来 111 个候选只被拦掉 37 个（做多放行 34/56、做空 40/55）。
- **死胡同**：先跑回测再解释净 R 从 −11.02 降到 −8.41，会把"少亏 2.61R"读成过滤器有效。真正的原因是入场数 75→58，每笔净收益 −0.1469→−0.1450R 几乎没动，见 [`fewer-trades-can-improve-equity-with-worse-per-trade-expectancy.md`](fewer-trades-can-improve-equity-with-worse-per-trade-expectancy.md)。
- **有效路径**：先在**已有信号根**上统计新指标的分布，再决定值不值得回测。整根收盘位于 BB200±2σ 之外、且 Stoch5/3/3 在 20/80 极区的 K 线，其 RSI14 中位数本来就是做多 28.42、做空 75.37——已经贴着 30/70 阈值。同一根 K 线的"极端位置"被三个指标同时描述，门只能切掉分布尾巴。
- **通用规则**：加过滤器之前，先输出该指标在现有信号根上的分布与预计拦截率。拦截率很低（这里 33%）说明它与原信号共线，回测差异主要是样本量差异，不是新信息；要判断信息量必须另做与占仓无关的事件级检验，见 [`serial-occupancy-hides-whether-a-gate-picks-better-signals.md`](serial-occupancy-hides-whether-a-gate-picks-better-signals.md)。
- **牵连**：`yoyo/evaluation/bb_stoch_rsi_study.py` 的 `counts.long/short.median_rsi`、`experiments/active/exp-eth-bb-stoch-rsi-filter-20260916-v1/results.json`、Pine `eth_bb_stoch_strategy_v3.pine`。阈值 14/30/70 取公开源码默认值，未核实 Owner 图上实参。
