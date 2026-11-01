# Choosing for maximum profit under a drawdown cap overshoots the cap out of sample

- **问题**：触价收盘幸存切片（评分 ≥0.5、收盘仍在 1.618 外、收回 <64%）20 份 3 倍从 2025 年起只到 1695U。Owner 要求“以最大利润为目标，回撤小于35%”重构策略（`exp-monday-limit-close-optimize-20261101-v1`）。
- **死胡同**：同时搜评分门槛、收回比例、五种止盈、两档止损和 40 种仓位，挑训练期内回撤 <35% 的最大利润，再按年滚动套到下一年。每年选出的规则都不一样（对侧 1.618、评分 0.45、收回 ≤0.3/0.5，3–5 份 5 倍），训练期回撤都贴着 33–34%。套到下一年：2024 年 5.35x、回撤 25%；2025 年 6.42x 但回撤 69%；2026 年 0.95x、回撤 69%。三年连起来 1000U→32,814U，回撤 72%。全历史一起挑能到 68x、回撤 30%，但这只是同一段数据挑出来的上限。放宽评分或收回比例多出的单，在 2025–26 年每笔都亏（−21～−56bp）。
- **有效路径**：入场条件固定为已经过检验的那一组，只改仓位。按 2020–24 回撤 <35% 挑，选到 2 份 3 倍（每笔名义 150%），2025 年起回撤 55%；按 2020–24 回撤 <17.5% 挑，选到 10 份 5 倍（每笔名义 50%），2025 年起 5.35x、10 种排序里最大回撤 19%。两次样本外回撤都约是训练期的 1.6–1.7 倍。
- **通用规则**：带回撤上限找最大利润时，被挑中的组合总是贴着上限，样本外回撤会明显超出。先固定已经验证过的入场条件，再只用之前的数据挑仓位，并给回撤留余量（这里按上限的一半）；不要每年按最大利润重挑过滤条件。“最大利润”主要来自仓位，不来自换条件。
- **牵连**：`yoyo/evaluation/monday_limit_close_optimize.py`（`--sizing` 是看到滚动结果后补做的）、`exp-monday-limit-close-survivor-paths-20261101-v2`；2025–26 已用于选出幸存切片，不是完全没看过的数据。相关：[touch-bar-information-pays-only-before-the-price-recovers.md](touch-bar-information-pays-only-before-the-price-recovers.md)、[alarm-threshold-quality-and-trade-count-peak-in-different-places.md](alarm-threshold-quality-and-trade-count-peak-in-different-places.md)。
