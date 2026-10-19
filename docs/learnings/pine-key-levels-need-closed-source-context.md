# 跨周期关键位必须在固定源周期里完成状态计算

- **问题**：给《周一高低点区间》增加昨日成交量分布、nPOC 和上周极值时，直接使用图表 K 线会让切换周期改变成交量分配结果，也可能把未收盘数据当成已确认价位。
- **死胡同**：只把 15m OHLC 通过 `request.security` 取到高周期图表后再聚合，拿到的只是抽样子 K 线，会遗漏成交量、周极值及 nPOC 回踩。把自然日写死成 96 根也无法覆盖纽约的夏令时切换日。
- **有效路径**：在固定 15m 请求上下文内部维护完整的日、周和 nPOC 状态，图表只接收快照。小于 15m 的图表使用快照 `[1]` 配合 `lookahead_on`；大于等于 15m 时取最后状态，并在实时源 K 线未收盘时退回上一份快照。日、周边界由所选时区的日历生成；在最后一根完整 K 线收盘时冻结，避免等到翌日首根结束。数据缺口使旧 nPOC 的“未触及”证据失效，并重置连续覆盖起点。
- **通用规则**：先明确数据源周期、时区、确认时间和缺口语义，再设计跨周期请求。原生测试必须调用交付的 Pine 函数，至少验证跨日、跨周、回踩、缺口和 23/25 小时自然日。Python 源码检查不能代替 Pine 编译与运行。
- **牵连**：`yoyo/evaluation/pine/monday_range_v1_11.pine`；`tests/evaluation/monday_range_v1_11_native_checks.pine`；80 档、70% 价值区与 `yoyo/monitor/market_context.py` 保持同一估算算法，不能宣称等同逐笔或 TradingView 原生成交量分布。依据：[Pine 跨周期请求](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)、[Pine 时间](https://www.tradingview.com/pine-script-docs/concepts/time/)。
