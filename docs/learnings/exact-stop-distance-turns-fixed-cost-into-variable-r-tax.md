# 精确 K 线止损会把固定百分比成本变成可变 R 税

- **问题**：BTC 1H K1→K2 回放在零成本下有正毛 R、等名义毛复利也明显为正，但扣统一 0.2% 后几乎归零，按固定账户风险定仓反而大亏。
- **死胡同**：只看 3R/1R 的名义赔率，默认每笔成本只是固定减 20bp；这样看不到 exact-K2 极值止损只有约 0.30% 中位距离时，20bp 已经相当于约 0.66R，而且每笔 R 税随止损宽度变化。
- **有效路径**：同一账本同时报告价格收益、毛/净 R 与 `cost_r = round_trip_cost / (risk_price / entry_price)`，再分别重放等名义与等风险仓位；本例毛 R 均值 +0.445R，被平均 0.717R 成本吃成净 -0.272R。
- **通用规则**：凡是止损取逐笔 K 线极值、目标按 R 倍数定义，第一步就计算成本的 R 分布；在决定成本和仓位口径前，不能用“3R 目标”推断盈亏平衡胜率或实盘期望。
- **牵连**：`scripts/backtest_two_key_candle_pine_v8_btc_1h.py`、Pine v8 exact-K2 stop、3R/12h、固定 0.2% 往返成本；呼应 [毛优势、成本与泛化必须分开诊断](gross-edge-must-be-separated-from-cost-and-generalization.md) 与 [ATR 等比障碍的固定成本效应](atr-scaled-barriers-vs-fixed-cost-fake-an-edge.md)。
