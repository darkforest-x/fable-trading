# 时间箭头不能让用户误读为成交价格

- **问题**：2026-10-29 Owner 指出 spike 的 HEMI 回看箭头出现在 1.618 之上。原 `near_P18` OHLC/OLHC 两份成交记录均为买入 0.01115564，恰等于当周下 1.618；图表却把账本 11:50 时间戳画在该分钟 K 线下方。
- **死胡同**：只把箭头命名为“开仓时间”不足以消除误读。`belowBar` 的纵坐标不是成交价；原 5m 收盘记账时间也不是精确盘中成交时刻。此前已有时间精度教训，但本次前端投影未带成交价和观察区间，仍然重现了问题。
- **有效路径**：沿资金账本的事件键回查原成交文件，再核对 `entry_i=106263` 的原行情与框架时间转换。原观察柱为北京时间 2026-09-02 11:45–11:50；真实 11:45 的 1m 最低 0.01112，触及 0.01115564。框架用该 5m 柱结束时间记账，前端又以开盘索引定位，产生横向偏移。本次完成诊断，未改前端或重算成交。
- **通用规则**：复盘标记的横轴必须说明成交时刻或观察区间，纵轴必须明确实际成交价或仅作时间提示。不能用任意 K 线下方的箭头冒充成交点，也不能用分钟首次碰价替换冻结框架成交时刻。
- **牵连**：`yoyo/monitor/strategy_backtest.py`、`yoyo/monitor/static/reclaim-chart.js:88`、`yoyo/evaluation/monday_limit_v3.py:73`；原证据为 `exp-monday-limit-v3-grid-20261025-v1/{ohlc,olhc}/symbols/HEMIUSDT/trades.csv.gz` 中 `HEMIUSDT:2026-08-31:+1:m60:near_monday_edge:c0:v_near_P18`。关联 [成交柱结束时间](candle-close-timestamps-must-not-shift-review-markers.md) 与 [更细 K 线的成交精度](finer-candles-do-not-create-finer-fill-timestamps.md)。
