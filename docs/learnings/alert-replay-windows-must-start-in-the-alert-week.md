# 报警回看窗口的起点必须落在报警那一周

- **问题**：全市场报警历史要能点进去看“报警后的走势”。复用模拟盘历史交易的 `/api/monday-fib/trade-candles`，把报警当成一笔“交易”（entry=首次触及，exit=报警后若干根）。
- **死胡同**：想多看报警前的行情，就把 entry 往前挪 36 根。但后端用 `levels.read_display(symbol, entry_ms)` 选周一锚点：4H 周期下挪 36 根就是 6 天，会落到上一周，画出来的是上一周的 1.618，和触发报警的线不是同一组。另外 `monday-fib-chart` 在 `paperHistory` 模式下用 `displayWindow` 把显示裁到 entry 前 12 根，所以往前多取的 K 线根本显示不出来。
- **有效路径**：entry 固定为报警首次触及的时间，接受只显示报警前 12 根；exit 取 `min(最后触及 + 96 根, 最近一根已收盘 K)`，并要求报警所在 K 线已收盘（否则后端会报“开平仓时段的已收盘 K 线暂不可用”，前端改为提示换更小周期）。报警时刻用 `series.setMarkers` 标记，第一次绘制时把可见范围设成整个窗口。用本机真实数据核对：后端返回的 `week` 和前端 `weekAt(first_ms)` 一致，322 根 15m K 线带当周的 1.618。
- **通用规则**：复用一个按“交易”设计的取数接口前，先看清它的每个时间参数还决定了什么（锚点周、显示裁剪），不要只当成“取 K 线的起止时间”。想多看上下文，应该改显示层，不能挪动决定锚点的那个时间参数。
- **牵连**：`yoyo/monitor/static/spike-alarm.js`（`replayWindow`、`markAlert`）；`yoyo/monitor/monday_chart_history.py::trade_window`（报警前 240 根、报警后 80 根、5000 根上限）；`yoyo/monitor/static/monday-fib-chart.js` 的 `displayWindow`；测试 `tests/monitor/frontend_spike_alarm.test.cjs`。相关：[固定日历的价格锚点](calendar-price-anchors-must-use-a-fixed-source-context.md)。
