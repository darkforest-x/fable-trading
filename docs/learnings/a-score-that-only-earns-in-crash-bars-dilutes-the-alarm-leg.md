# A score that only earns inside crash bars dilutes the alarm leg it was meant to complete

- **问题**：25 币报警只能抓报警 K 线之后的触价，像 2026-08-22 这种一根 5m 就跌完收回的插针抓不到；触价前评分 ≥0.6 挂单正好抓到这些 K 线里的触价。Owner 要求把两者合成一个账户回测（`exp-monday-limit-alarm-combo-20261031-v1`）。
- **死胡同**：直觉是"两腿各抓一类机会，合起来只会更好"。按评分腿的总均值（near30 +65bp，opp30 +240bp）看也像能加分。但合并后 near30 3.56x DD40 不如只做报警 3.70x DD17；near18 2.43x 不如 2.92x；opp30 虽然 6.49x，回撤 58%，且 2026 年不如不看评分的对照组（1.37x vs 1.70x），2025 年三种规则合并都约 1.0x。
- **有效路径**：先按时间切开评分腿：239 笔落在 19 根报警 K 线内（+324bp），43 笔在报警后（+374bp），其余 674 笔分布在 657 根普通 K 线上（-47bp）。评分腿的利润几乎全来自 19 根事先无法预知的崩盘 K 线；合进账户后，普通时间的 674 笔亏损单持续占用 N20 仓位、拉大回撤，而那 19 根 K 线又没有多到能弥补。
- **通用规则**：把一条腿并入另一条之前，先把它的收益按"能否事前知道的时间状态"拆开。如果正收益只集中在少数事先识别不了的时刻，合并只是在每个普通时刻多买了一张亏损彩票；要么找到事前可见的条件把这条腿限制在那些时刻，要么不加。
- **牵连**：`yoyo/evaluation/monday_limit_alarm_combo.py`（rest_by_timing）、`exp-monday-limit-alarm-entry-20261029-v1`、`exp-monday-limit-score-leak-audit-20261027-v1/scores_causal.csv.gz`；相关：[a-market-wide-alarm-selects-touches-the-score-rejects.md](a-market-wide-alarm-selects-touches-the-score-rejects.md)。
