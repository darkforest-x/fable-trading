# 极小止损的 R 倍数取决于滑点，不能拿来复利

- **问题**：Owner 拿一笔 MUBARAK 5m 单子举例：止损放在插针影线下，只有 0.22%，止盈 4.6%，约 20R。他问为什么不用 5 分钟级别。
- **死胡同**：只看平均 R。全部 626 个山寨币按较差K线顺序、扣 10bp 止损滑点后，平均 +4.09R，看上去非常好；按每笔风险 1% 复利，1000U 能到 1.8e17 倍。但同一批单子按名义金额等额计算，每笔是 −13.5bp，亏损。正的 R 几乎全部来自止损不到 0.15% 的单：平均 +13.65R，中位数却是 −3.32R。原因是手续费加滑点（约 0.17%）比止损距离本身还大，一次止损实际亏 3R 以上；要把这类单做到风险 1%，名义仓位得开到权益的几倍。
- **有效路径**：分别按名义等额和风险等额两种口径计算，按止损距离分档，并把止损滑点设为 10、20、30bp 三档。结果：滑点 20bp 时平均降到 +2.15R，复利直接爆仓；30bp 时只剩 +0.21R。插针行情中止损单的真实滑点往往不止 10bp，所以这个优势不可靠。
- **通用规则**：凡是止损距离可能小于"手续费 + 合理滑点"几倍的策略，必须同时报告：名义等额收益、按止损距离分档的 R、滑点敏感性，以及杠杆上限下的复利结果。不能只看平均 R 或单笔大 R 的例子。
- **牵连**：`yoyo/evaluation/monday_limit_wick_stop.py`、`yoyo/evaluation/monday_limit_wick_compare.py`、`experiments/active/exp-monday-limit-wick-compare-20261025-v1/`。相关笔记：[per-trade-edge-does-not-compound-when-trades-overlap.md](per-trade-edge-does-not-compound-when-trades-overlap.md)。
