# Pine 隐藏诊断输出也消耗绘图配额，必须上图验证

- **问题**：V7 编译保存通过，上图却报 RE10140，实际计数 71 超过上限 64。
- **死胡同**：仅统计 plot 调用数量并检查编译。一个调用可能消耗多个计数，数据窗口专用输出也不会被豁免；设置 display.none 同样不能解决配额问题。
- **有效路径**：删除 17 个仅用于内部追溯的 Data Window plot。比较源码证明没有计算或可见绘图变更，再在 TradingView ETH 15 分钟图确认通道、风险框、主副图实际显示且错误消失。
- **通用规则**：Pine 增加可视化前先盘点隐藏诊断与动态颜色的配额，保留余量；验收必须同时包括保存、编译和真实历史数据上图运行。
- **牵连**：`yoyo/evaluation/pine/spike_burst_v7.pine`；`docs/spike_v7_bb_channel.md`；[TradingView 官方绘图限制](https://www.tradingview.com/pine-script-docs/writing/limitations/#plot-limits)。
