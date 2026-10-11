# “错几次总有一次会起来”的重复进场，要按形态加总、在全部形态上评估

- **问题**：Bit浪浪视频的 5 分钟拐点进场说“被止损也没关系，错一次两次三次，它总有一次会起来”。在视频挑出的 PEOPLE（2024-05）和 SOL（2023-10）回调段里机械执行，确实分别先止损 5 次、3 次，然后抓到大行情。
- **死胡同**：只看这两个案例会得出“重复试错的总账为正”。但案例是按事后涨了挑出来的，“总有一次”在定义上就成立；逐笔看胜率 26%、计划盈亏比中位 6.4R，也像是能赚钱。
- **有效路径**：把所有符合条件的形态都扫出来（exp-bitlanglang-mainwave-20261011-v1：542 个币、643 个形态、1736 笔），把同一形态内的多次进场加总成“形态 R”。结果只有 32% 的形态加总为正，平均每个形态 −0.31R；2025 年起逐笔 −0.31R，比匹配随机和同确认随机都差。≥5R 的大单占 3.9%、贡献 +664R，但抵不过 74% 的直接止损。
- **通用规则**：凡是“错了就再进，直到对一次”的入场法，评估单位是形态（把重试加总），样本是全部满足条件的形态，不是演示用的赢家；同时报告每个形态平均重试几次和形态胜率。计划盈亏比只是开单门槛，看的必须是实际兑现的 R。
- **牵连**：yoyo/evaluation/bitlanglang_mainwave_v1.py（MAX_ATTEMPTS=3、setup_mean_net_r / setup_positive）；相关 [cost-saving-by-abstention-does-not-establish-positive-entry-edge.md](cost-saving-by-abstention-does-not-establish-positive-entry-edge.md)、[flagship-trade-profit-must-be-attributed-from-fill-averages.md](flagship-trade-profit-must-be-attributed-from-fill-averages.md)。
