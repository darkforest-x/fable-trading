# Pine 回测必须区分待成交订单与已成交持仓

- **问题**：把冻结指标的信号块接入原生 `strategy()` 时，新反向信号在收盘提交，但旧持仓在此刻仍未平掉。若立刻覆盖持仓 ID、初始止损与方向，随后计算的保护线会混用旧成交价与新订单方向。
- **死胡同**：只验证信号代码逐行一致，不能证明回测账本正确。同根同时调用旧仓 `strategy.close` 和反向 `strategy.entry`，还可能重复处理自动反转；入场当根即触发止损的订单，也不能依赖“曾观察到非零持仓”才能清理待成交状态。
- **有效路径**：在 TOTAL2 原生研究脚本中，先按独立订单 ID 归约已平交易并清理匹配的 pending/active 状态，再确认实际成交并管理旧保护；最后提交新订单。合格反向入场仅由自动反转完成，未通过入场门的原始反向信号才单独平旧仓。函数返回贡献值，由 Pine 全局作用域累计统计，避免在函数内修改全局标量。
- **通用规则**：信号一致、订单提交、真实模拟成交与统计聚合是不同检查点。必须覆盖入场当根止损、反向确认、被入场过滤的反向退出、同向重复四种路径。静态核对不等于已通过 TradingView 编译或完成历史回测。
- **牵连**：`experiments/active/exp-spike-v8-total2-1h-native-20260914-v1/spike_v8_total2_1h_native.pine`；TradingView 默认收盘计算、下一可用 tick 成交。TOTAL2 为市值指数，原生虚拟数量与资金不能冒充可交易账户收益。

参考：[TradingView Strategies 官方文档](https://www.tradingview.com/pine-script-docs/concepts/strategies/)。
