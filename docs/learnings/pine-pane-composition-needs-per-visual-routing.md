# Pine 主副图合并要逐个指定绘图位置

- **问题**：把周一区间 V1.8 与翻身 V1.1 合并为 V2.0，既要保留价格主图上的区间、Fib、K 线着色和勾叉，也要保留完整 Stoch／WVF 副图。只确认公式相同不足以验收，错误的绘图位置会污染坐标轴。
- **死胡同**：设计阶段排除了直接拼接两个 `indicator()` 声明，以及把整个脚本设为 `overlay=true` 的方案；这两条没有实际提交给编译器。前者不能构成一个脚本，后者无法保留独立振荡器面板。切换到 1H 时还一度因旧价格坐标范围看不到 K 线，重置图表视图后恢复，不能把该 UI 状态误诊为计算错误。
- **有效路径**：只保留一个 `overlay=false` 声明，移除副图脚本声明层的 timeframe 参数；给区间的 8 个价格绘图构造调用及原有 2 个主图勾叉指定 `force_overlay=true`。Stoch／WVF 的 30 个变量仅做命名空间隔离，字符串与公式不改。逆向撤销这些机械改动后，两部分分别与旧版源码逐字一致。TradingView 原生编译成功，5m 新旧同屏数值一致，1H 可见历史 Fib 和同根箭头／勾叉。
- **通用规则**：合并前先列出每个视觉元素应使用的面板和坐标轴，再决定唯一脚本声明。把公式保真、原生编译、绘图归属与历史对象保留分别验收；图表显示成功不代表组合交易规则已验证。
- **牵连**：`yoyo/evaluation/pine/monday_range_v2_0.pine`；原版 `monday_range_v1_8.pine` 与 `owner_fanshen_v1_1.pine` 保留。交付回执在 `output/pine/monday_range_v2_0_delivery.json`。Owner 仅为本次合并明确豁免 Stitch。依据：[TradingView visuals overview](https://www.tradingview.com/pine-script-docs/visuals/overview/) 与 [declaration statements](https://www.tradingview.com/pine-script-docs/language/declaration-statements/)。
