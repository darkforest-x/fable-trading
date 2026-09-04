# 完整 K2 形态必须拆成收盘确认与下一开盘两个 Pine 事件

- **问题**：K2 的影线、实体和收回位置只有在 K2 收盘后才完整，但精确止损风险又定义为下一根 open 到 K2 极值；若在同一个布尔条件里同时画 K2 和入场，很容易把 K2 open 当成事后入场，或在 K2 收盘提前使用尚未出现的 next open。
- **死胡同**：把截图中的仓位框直接翻译成“在 K2 开盘入场”，再用完整 K2 形态过滤。这会让该小时后半段的信息反向决定小时开盘时的交易，视觉上贴图、因果上前视。另一个死胡同是在 K2 收盘先用 close 近似 next open 做风险门，导致历史与实时契约不同。
- **有效路径**：先用 barstate.isconfirmed 产生 K2-confirmed 预警，只冻结 K1、K2、gap 与形态分组件；下一根用 barstate.isnew 读取已知 open，才计算到 K2 极值的风险并产生正式 entry、stop、target。形态确认和可执行入场是两个明确事件。
- **通用规则**：只要信号需要完整当前棒形态，而成交口径是下一开盘，Pine 实现第一步就画出两阶段时间线；所有依赖 next open 的过滤、去重、仓位与告警都放在第二阶段，不能用当前 close 代替。
- **牵连**：experiments/active/exp-two-key-candle-feature-atlas-v3/pine/fable_two_key_candle_sma40_retest_v1.pine；scripts/validate_two_key_candle_pine_indicator.py；barstate.isconfirmed；barstate.isnew；K2 极值止损；参见 [a-price-after-the-signal-is-not-a-fill-unless-it-is-after-the-decision.md](a-price-after-the-signal-is-not-a-fill-unless-it-is-after-the-decision.md)。
