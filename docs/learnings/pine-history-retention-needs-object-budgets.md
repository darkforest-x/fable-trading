# Historical ranges and rich annotations need separate object budgets

- **问题**：Monday Range v1.8 将高低点和历史 Fib 共用 65 周上限，远期区间随注释一起消失。
- **死胡同**：只调大周数会突破 TradingView 的 500 条线／标签上限；按 bar_index 延长历史还会碰到向过去绘图的距离限制。按单条 Fib 删图会留下半周注释。
- **有效路径**：新建 v1.9，独立保留 H/L 和历史 Fib；根据实际比例数量预算，分配前按整周清理；用时间戳定位历史。显示请求上限与实际已加载周数，避免把参数当成行情覆盖证据。
- **通用规则**：扩展历史前先算对象成本，并区分数据覆盖、轻量结构和昂贵注释的生命周期。
- **牵连**：`yoyo/evaluation/pine/monday_range_v1_9.pine`；v1.8 保留。官方限制：https://www.tradingview.com/pine-script-docs/writing/limitations/ 。
