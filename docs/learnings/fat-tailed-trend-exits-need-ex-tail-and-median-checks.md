# 趋势跟随出场的正均值可能全靠前 1% 的单子，均值检验要配去尾均值和中位数

- **问题**：12H / 4H 云带信号（固定止损 + 盘中 EMA 反向离场、无止盈），2020–2026 平均 +77 / +66bp/笔，并且显著跑赢同周匹配随机入场（+115 / +173bp，周聚类 CI 在 0 以上），看起来像有优势。
- **死胡同**：直接比较 top 组与全体的均值，加置换检验。12H 2025 年 top 20% +149bp，但置换 p 只有 0.52；全体 +133bp，可 2024 年是 −134bp。均值逐年剧烈跳动，原因是 EGLD、KSM 在 2020–21 年涨 10 倍，以及 2025 年 ALPACA 暴拉（单笔 +26 万 bp），这几笔单子主导了结果。
- **有效路径**：先报尾部集中度：前 1% 单子的收益合计是全体总和的 1.95 倍（12H）和 1.58 倍（4H）；去掉这 1% 后平均为 −74 / −39bp，中位数 −397 / −184bp。再看 1/99% 截尾均值和中位数在 top 组与全体之间的差别；显著性只认按周分层的置换检验，不认逐笔 t。结论：信号择时比同周随机好，但规则本身只靠少数大趋势单撑着，评分模型并没有挑出这些单子。
- **通用规则**：凡是无止盈、可以长期持有的出场，结果表里同时报：均值、前 1% 收益占比、去掉前 1% 后的均值、中位数、截尾均值。正均值只要来自不到 1% 的单子，就按“依赖极端行情”标注，不当作可复现的边际优势。
- **牵连**：`experiments/active/exp-signal-center-score-20261106-v1/summary.json` 的 `census.*.top1pct_sum_bp / mean_excluding_top1pct_bp`；相关 [extreme-r-needs-cost-units-and-underlying-event-audit.md](extreme-r-needs-cost-units-and-underlying-event-audit.md)、[win-classifier-auc-can-come-from-rule-mechanical-exits.md](win-classifier-auc-can-come-from-rule-mechanical-exits.md)。
