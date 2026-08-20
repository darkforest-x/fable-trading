# Pine 下单距离与止损 tick 距离是两个量

- **问题**：Pine 用未舍入的 `signalStopDistance` 计算风险杠杆，却把同一距离另行 round 成 tick 用于保护单；Python 先 round 再同时用于两者，导致数量和资金路径有微小偏差。
- **死胡同**：看到两个量源自同一 ATR/百分比公式就复用一个变量。tick 对齐是订单价格约束，不代表信号时刻的风险公式也自动量化。
- **有效路径**：显式保留 `raw_stop_distance` 与 `rounded_stop_distance`；前者只进入目标杠杆/数量，后者只进入初始 stop 价格，并用非整数 tick 的合成例验证差异。
- **通用规则**：翻译 Pine 时逐个记录表达式求值和取整发生的时点；“数值很接近”不能代替逐字段 parity。
- **牵连**：`ExecutionParameters`、`simulate_symbol()`、Docker replay、反手 signal-equity 数量、收益/回撤和 TradingView trade-export 对账。
