# Pine 历史框需要独立于绘图容量的交易记录

- **问题**：V2.5 允许同一 1H 观察下多次 15m 入场，但只保留最近 N 个已结束参考，向左查看旧行情没有交易框。源窗口实际算出 146 笔，不能把画面只有 10 笔解释成历史只有 10 笔。
- **死胡同**：只增加 N 会把截断边界向前推，仍不能按历史窗口查看；按可见范围裁剪交易计算则会丢失窗口之前形成的观察状态或仍存续的单，改变结果。把所有历史记录拼成一个巨型字符串还会遇到 Pine 单字符串长度上限。
- **有效路径**：保留源窗口完整归档，仅用未结束对象列表推进交易状态；最近两个确认快照以字符串数组传到图表层。在图表层读取可见左右端，记录右端 K 线实际 time_close，再选择与画面相交的交易生命周期。跨入左边界的单也保留，显示容量只筛绘图。超容量显示提示和可见总数，并披露数据覆盖起点。原生九月两个相邻窗口保留相同三笔的 12 字段，宽视口 109 笔中绘制 40 笔／200 个 box；全归档 146 笔的时间、价格、退出、方向校验值保持一致。
- **通用规则**：先区分数据覆盖、状态计算、记录留存、视口选择和绘图对象预算。可见范围变化只能影响显示选择，不能影响入场或退出。跨周期边界要用图表实际收盘时间，不能用右端开盘时间截断高周期 K 线内部事件。
- **牵连**：`yoyo/evaluation/pine/monday_range_v2_6.pine`、`tests/evaluation/monday_range_v2_6_native_checks.pine`、`tests/evaluation/test_monday_range_v2_6.py`；回看仍受固定 30000 根 5m 请求及 TradingView 可用历史约束。每框最近 40 次跟踪路径保留规则继承旧版。官方文档：[可见范围重算](https://www.tradingview.com/pine-script-docs/faq/techniques/#how-can-i-plot-the-highest-and-lowest-visible-candle-values)、[Pine 限制](https://www.tradingview.com/pine-script-docs/writing/limitations/)。
