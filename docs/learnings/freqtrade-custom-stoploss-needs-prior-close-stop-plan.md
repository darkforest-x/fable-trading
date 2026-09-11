# Freqtrade callback order requires a prior-close stop plan for candle parity

- **问题**：SPIKE V6 的保护价在本根收盘才更新、下一根才生效；Freqtrade 2026.8 回测会把 `custom_stoploss` 的 `current_rate` 设为当前长仓 high / 空仓 low，再在同一根检查反向极值。
- **死胡同**：在 callback 里从当前 OHLC 重新计算追踪止损，会把收盘后的新保护价回填到同根 low/high，制造 Pine 没有的同 bar 止损。
- **有效路径**：逐根预计算每个 candle open 已知的 absolute stop。callback 只按 `(entry_time, current_time, side)` 查该值，并以 `stoploss_from_absolute` 转换；当前 OHLC 只决定既有 stop 是否触发，收盘计算的 ratchet 写给下一行。
- **通用规则**：把框架 callback 的传入价格当作执行上下文而不是信号特征。任何闭合 bar 策略接入回测器前，先测试“追加未来不改历史信号/active stop”和“本根更新不影响本根 stop”。
- **牵连**：`experiments/active/exp-spike-v1-eth-stops-20260911/freqtrade_v6/build_v6_bridge.py`、`strategies/FrozenV6Bridge.py`；Freqtrade 2026.8 `strategy/interface.py` 的 `ft_stoploss_adjust` / `ft_stoploss_reached` 调用顺序。
