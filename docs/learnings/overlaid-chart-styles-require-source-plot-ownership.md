# 叠加图表的样式复刻必须先确认绘图归属

- **问题**：同一张 TradingView 图叠加了多套指标，只按截图识别颜色时，把另一套“双均线”的蓝线误认成 `Moving Average Shift [ChartPrime]` 的均线，导致复刻后的均线和 K 线样式都偏离参考。
- **死胡同**：从抗锯齿截图像素和视觉位置猜颜色。叠加线交叉、指标图例折叠、旧实例样式缓存都会让“看起来属于它”的线不一定真由它绘制；即使色值接近，也无法还原动态着色条件、线宽和光晕。
- **有效路径**：先在图例中锁定目标指标，再打开其只读 Pine 源码；从源码同时提取输入、状态条件、颜色常量和完整绘图调用。本例的真实契约是 `source=hl2`、`MA=SMA40`、`source >= MA ? #17A297 : color.orange`，同一状态色用于均线与 OHLC，均线由 2 px 主线和 7 px/80% 透明光晕组成。
- **通用规则**：复刻叠加图表里的现成指标时，第一步不是取色，而是建立“图例项 → 源码变量 → plot/barcolor/plotcandle”归属链；源码可读时，截图只用于验证最终外观，不用于决定实现参数。
- **牵连**：`experiments/active/exp-two-key-candle-feature-atlas-v3/pine/fable_two_key_candle_sma40_retest_v1.pine`、TradingView 已保存脚本版本、移动端旧实例的样式状态。
