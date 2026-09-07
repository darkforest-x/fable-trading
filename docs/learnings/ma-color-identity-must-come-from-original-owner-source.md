# 旧均线的颜色身份必须回到 Owner 原始源码确认

- **问题**：Owner 问均线密集系统里“之前最黑色的线”是哪条，并希望突出蓄势区的影线回踩；当前 IMACD 已重设计配色，颜色记忆与现有图表不再一一对应。
- **死胡同**：先从当前灰色长均线推测 SMA120，或借用另一套回踩系统的 SMA40，都会把视觉相似当作指标身份。当前灰120是后来样式改动；SMA40(hl2)属于另一套系统，不能代替原六均线口径。
- **有效路径**：沿 IMACD＋均线密集待办的 Notion 父页读取两版旧源码。两页均默认 `maPeriod1=20`、`sma20=ta.sma(close, maPeriod1)`，并用不透明 `color.black`、2px 绘制 SMA20；EMA20 则为黑色50%透明、1px，60组蓝色、120组紫色。因此旧默认色板中的“最黑线”对应 SMA20(close)，而不是由现有灰线反推出来的120线。
- **通用规则**：把“旧颜色→源码变量→周期与价格源”连成证据链，再判断用户是否指该套默认色板。颜色身份可高置信确认，但不能因此声称价格“通常”或“必定”回踩该线；本次已读页面没有支持这种发生频率的统计，也没有核验用户当前图表的参数覆盖。
- **牵连**：[本次短摘录证据](../../experiments/active/exp-imacd-ma-mtf-20260907-v3/pine_indicator/sma20_reference_20260908.json)；Notion [指标优化迭代-20251122](https://app.notion.com/p/2b38856479af8011a6bfe3dafe5ea06c)、[指标更新-20251203](https://app.notion.com/p/2bd8856479af80f288e0e214548f705b)；[SMA40 系统归属记录](overlaid-chart-styles-require-source-plot-ownership.md)。本笔记只记录身份溯源，不包含新 UI 版本验收或回踩收益验证。
