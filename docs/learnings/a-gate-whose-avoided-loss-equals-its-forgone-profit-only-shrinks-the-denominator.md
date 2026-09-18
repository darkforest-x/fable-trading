# 避开的亏损 ≈ 错过的盈利的门，只是在缩分母

- **问题**：SPIKE V10（重做）在 V9 多头上加「趋势线突破后 ≤7 根」的门，全期每笔净 R +0.0299→+0.0752、PF 1.043→1.111、事件回撤 4,174R→1,711R，三项一起变好，看上去像门在挑好信号。
- **死胡同**：读每笔 R / PF / 回撤的改善。三者都是被笔数驱动的：总量不变、笔数砍到 40%，每笔均值自然升高；叠加事件回撤随事件数下降，也和信号质量无关。配对超额 +0.0695→+0.0957 同样偏高，但 p 从 0.151 到 0.154 纹丝不动。
- **有效路径**：把门的差额按删掉的事件拆开——避开的亏损 13,760R、错过的盈利 14,050R、腾出仓位后新入场 +290R，净差 −0.3R。**两头等量削掉 = 门对结果不带信息**。再看分段：后段门让每笔从 −0.2102 变成 −0.2448、超额从 −0.0549 变成 −0.1204，信息是负的。
- **通用规则**：任何入场门先出「避亏 / 错过盈利 / 新入场」三项归因表，并强制它与总净 R 差额对账；避亏与错过盈利之比接近 1 时，每笔指标的改善一律视为分母效应，不写成「门有效」。与 [fewer-trades-can-improve-equity-with-worse-per-trade-expectancy.md](fewer-trades-can-improve-equity-with-worse-per-trade-expectancy.md) 是同一件事的镜像：笔数变化会单独推动总量或均值中的一个。
- **牵连**：`yoyo/evaluation/spike_v10_long_report.py::attribution`（对账不上就 raise）、`experiments/active/exp-spike-v10-long-break7-20260918-v1/statistics/attribution.csv`、`analysis/p1_spike_v10_long_break7_20260918.md` §4。
