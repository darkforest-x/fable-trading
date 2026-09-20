# 均线过滤默认开启，不代表参考线已经默认画到主图

- **问题**：Owner认可5m对应15m EMA120的画线，要求15m对应1h SMA60也默认显示。V12.4中SMA过滤默认开启，但该plot只有`display.data_window`。
- **死胡同**：仅检查`input.bool(true)`或状态面板会误以为参考线已交付；之前报告中的“显示SMA60”也不足以证明主图存在这条线。
- **有效路径**：逐一核对过滤开关、plot范围和主图路由。V12.5增加独立默认开启的显示开关，将同一个已确认H1数值画成紫色阶梯线并`force_overlay=true`；移除显示和版本变更后与V12.4逐字一致。
- **通用规则**：显示功能分别验收输入默认值、绘图目的地与可见图形；保持计算/入场规则独立，并通过实际图表核对。不要用历史交付描述替代当前源码和UI证据。
- **牵连**：`yoyo/evaluation/pine/spike_burst_v12_4.pine`保留；新版本`spike_burst_v12_5.pine`。本次未改变方向过滤、退出、ATR或成本。参考TradingView官方Plots文档：<https://www.tradingview.com/pine-script-docs/visuals/plots/>。
