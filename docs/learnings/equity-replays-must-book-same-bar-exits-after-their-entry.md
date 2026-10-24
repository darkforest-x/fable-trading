# 权益重放必须先处理开仓，再处理同一时间戳的平仓

- **问题**：2026-10-24 我用 Codex 1.618 挂单收回的框架成交，按事件时间重放 1000U 权益。结论"不加杠杆 0.98 倍、总仓位 ≤ 3 倍 1.25 倍"偏低，过滤后多个条件从 2022 年起权益几乎不动。
- **死胡同**：先怀疑是持仓太久、锁住了资金。查了持仓时长：中位数 1.7 小时，最长 14 天，排除。
- **有效路径**：按"时间相同则先平后开"排序，本意是先释放资金。但同一根 K 线内成交并止盈的单子，平仓时间戳等于开仓时间戳，于是平仓被排在开仓前面：
  - 平仓时，这笔的名义仓位还没分配，按 0 结算，利润丢失；
  - 随后开仓分配的名义仓位再也不会释放，总仓位上限被逐步占满。
  这类单子 1,868 笔，只占 3%，却贡献了 58% 的利润。修复方法是给事件分三类排序：其他单子的平仓 0、开仓 1、与自身开仓同一时间戳的平仓 2。用三笔交易的小例子手算核对，结果一致。修复提交 6327c20bd3。
- **通用规则**：事件驱动的重放，要先用一个"开仓即平仓"的小例子验证同时间戳顺序。另外要检查已结算利润合计是否等于逐笔收益乘名义仓位的合计；重放结果明显少于逐笔均值推算时，先查排序和资金释放。
- **牵连**：`yoyo/evaluation/monday_limit_reclaim_review.py` 和 `yoyo/evaluation/monday_limit_reclaim_filtered_replay.py` 中的 `replay()`。相关笔记：[per-trade-edge-does-not-compound-when-trades-overlap.md](per-trade-edge-does-not-compound-when-trades-overlap.md)、[same-bar-target-fills-under-ohlc-path-inflate-fast-spike-shorts.md](same-bar-target-fills-under-ohlc-path-inflate-fast-spike-shorts.md)。
