# 日历区间的完整性必须按市场交易时段判断

- **问题**：2026-10-28，Owner 的 SSE:601212 12 小时图加载「周一 1.618 V1.0」后没有线，并提示周一数据不完整。源码要求从所选时区周一午夜到周二午夜连续有数据，正常股票休市必然使 `complete` 为 false。
- **死胡同**：仅切换图表周期或把指标时区改为上海不能解决。12 小时本就在允许范围；上海周一的正常 48 根 5m K 线仍不从午夜开始、午间不连续，也不结束于次日午夜。这是源码及日程最小复现排除的路径，未声称在用户图表实际切换测试。
- **有效路径**：沿提示反查 `valid → full → complete → nextExpected`，找到午夜起点、逐根连续和午夜终点三项假设。标准库日程复现显示完整普通 A 股交易日也被拒绝。另核对纽约日界：2026-10-26 纽约周一对应北京周一 12:00 至周二 12:00，会纳入周一下午与周二上午；图表显示时区不改变指标输入。此次完成原因定位，未修改或发布 Pine。
- **通用规则**：跨市场复用日历指标时，先定义当地交易日和应有交易时段，再判断缺失。股票版本应区分午休、闭市、假日/停牌与交易时段内缺数据；不能删除所有完整性保护，也不能把休市的周一自动替换成周二。连续市场的原有检查保留其适用范围。
- **牵连**：`yoyo/evaluation/pine/monday_high_low_1618_v1_0.pine` 第 12、28、62–67、131–144 行；原交付 `artifacts/monday-high-low-1618-pine-20261026/delivery.json` 只验证 OKX:MUBARAKUSDT.P，已记录 session-gapped Mondays 会跳过。[上交所交易时段](https://english.sse.com.cn/start/trading/schedule/)；[TradingView 时间文档](https://www.tradingview.com/pine-script-docs/concepts/time/)。补充适用边界，不改写历史笔记 [日历区间必须先按细周期归属，再映射到图表](calendar-range-must-aggregate-intrabars-before-drawing.md)。
