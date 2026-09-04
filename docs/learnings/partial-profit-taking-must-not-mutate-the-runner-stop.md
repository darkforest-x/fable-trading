# 真实部分止盈不得暗中改写 runner 止损

- **问题**：用户要求趋势上涨中逐步兑现利润，但旧讨论容易把“提高整仓止损”当成“止盈”，导致图上看似锁盈，实际仍全部暴露在回撤、跳空和 bar 内路径风险中。
- **死胡同**：部分目标成交后同时把剩余仓位止损提到入场价或利润地板。这样无法区分收益来自真实减仓还是 stop ratchet，也会把“慢慢 TP”变成另一个动态止损实验。
- **有效路径**：把仓位和止损拆成两个独立状态。目标只扣减 `remaining_fraction` 并记录已实现收益；`active_stop` 只能由原 runner 规则更新。ETHUSDT.P 15m V16 在 +2/+4/+8/+12 signal ATR 各平 2.5%，四档全到仍保留 90%，部分成交代码块用静态测试保证不写 `activeStop`。
- **通用规则**：任何分批止盈实验都要分别记录已实现腿和剩余 runner；若部分成交会移动 stop，必须作为第二个变量单独预注册。图上“锁住利润”不等于发生了真实止盈成交。
- **牵连**：`fable_eth_15m_trend_gradual_tp_v16.pine`、`remainingPct`、`partialStage`、`activeStop`、stop-first 同根冲突顺序、20bp 往返成本；仓库 holdout 未读取。
