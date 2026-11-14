# TradingView 的 ta.pivothigh 允许左侧等高，右侧必须严格更低

- **问题**　Python 复刻 4H 主线时，枢轴高点按两侧都严格更低实现。V1.9 在 OKX:LPTUSDT.P 上比 Python 多出一条线，锚点在 2026-02-22 00:00：该根最高价 2.386，与左边一根相同。
- **死胡同**　旧移植 `trendline_break.py` 和 `trendline_v12.py` 都写着“TradingView 未公开平局规则”，只统计不采用。以前对齐的样本里恰好没有平局锚点，所以一直没暴露。
- **有效路径**　把 TradingView 日志里多出的锚点逐个代回原始 K 线，检查左右各 3 根。改成左侧 `>=`、右侧 `>` 后，LPT 9 条线、PIEVERSE 3 条线的建线时间和锚点与 TradingView 逐条一致。
- **通用规则**　复刻依赖 `ta.pivothigh` 或 `ta.pivotlow` 的 Pine 逻辑时，规则是左侧允许等高（低点允许等低），右侧严格。出现分歧时，先查锚点两侧是否有平局。
- **牵连**　`experiments/active/exp-trendline4h-line-quality-20261110-v1/trendline_v13.py` 和 `trendline_v12tv.py` 已按此规则实现。11-09 的普查、规律扫描和回测用的是严格规则：V1.2 线数 6072，按 TradingView 规则是 6196，结论方向不变，旧结果不改写。
