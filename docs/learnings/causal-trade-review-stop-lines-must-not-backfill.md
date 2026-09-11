# 逐笔复盘图的保护线不能从最终止损倒灌

- **问题**：V6 Freqtrade 复盘图把实际退出时的最终保护价画成贯穿全图的水平线，图形暗示入场前已知的止损，并且只画 close 折线，无法核对盘中触发。
- **死胡同**：用单条“effective protection”或只标初始止损看似简洁，但它丢失了每根 bar 的可用信息集，无法区分收盘后 ratchet 与同 bar stop 检查。
- **有效路径**：从冻结的 `stops_*` 计划按 `(entry_time, current_time, side)` 读取 `active_stop`，仅在 entry 到实际 Freqtrade exit 间以阶梯线绘制；初始 SL 也限制在同一范围。OHLC 实体和影线、信号收盘、next-open 实际成交与实际退出共用冻结 ledger 时间，退出后的 72 根只以复盘阴影标出。
- **通用规则**：交易图中的任何保护价都必须有逐 bar 时间索引；若没有历史计划，宁可不画轨迹，也不能用最终值回填。图中信号、成交和退出应直接来自同一交易 ledger，并明确哪些随后行情只是复盘。
- **牵连**：`experiments/active/exp-spike-v1-eth-stops-20260911/freqtrade_v6/render_cases.py`、`plans/stops_both_*_baseline.csv`、`analysis/p1_spike_v6_eth_freqtrade_20260911.md`。
