# Historical trade charts need fill-bounded data before viewport reuse

- **问题**：模拟盘历史成交沿用实时 K 线入口；开仓时点不在最近 720 根内时，移动视窗也找不到那笔交易。
- **死胡同**：只调整可视范围不能补回缺失的旧 K 线；直接复用回测数据入口又会把 OKX 模拟成交配到 Binance 行情。代码检查排除了这两条路径，未用伪造行情兜底。
- **有效路径**：按保存的开平仓时间读取有界 OKX 历史窗口，沿用回测的 240 根前置、80 根后置上下文和 `replayRange` 取景。历史视图独立缓存，不开启实时流；只叠加所选交易，切周期、暂停恢复和换币均保留来源及时间身份。
- **通用规则**：复用回放布局前，先确认数据窗口包含事件、来源对应记录，再复用视窗逻辑。通用行情滚动白名单不应成为查看已保存交易的限制。
- **牵连**：`yoyo/monitor/monday_chart_history.py`、`static/monday-fib-chart.js`、`static/reclaim-chart.js`、`static/monday-reclaim-paper.js`。只读展示，不改评分、成交、盈亏或扫描订阅。Stitch 画板 `069bd922d6c148638f3c3e85648a7394`。
