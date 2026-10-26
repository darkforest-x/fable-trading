# A "best hours / best weekdays" win-rate table needs select-then-confirm and cluster-level permutations

- **Problem**: The owner asked which weekdays and hours of the Monday 1618 limit-reclaim trades win most often, and whether US news matters. The full-history table (22,708 v2 trades) had obvious peaks: Beijing 05h 43.6 percent and 13h 42.0 percent against a 33.0 percent average, and FOMC windows at 45 percent.
- **Dead ends**:
  - Trade-level tests overstate significance. Dozens of alts fill in the same market move, so 22,708 trades come from only a few thousand independent clock hours. Shuffling labels per trade breaks those clusters.
  - Reading peaks off the full-history table selects the window and then grades it on the same data. The 05h peak was 32 percent in 2020-24 and 51 percent in 2025-26, so it was one period's noise.
  - Before the change, "win = net > 0" classified breakeven exits by float noise. Those exits are at entry +0.17 percent and net 0 ± 0.01bp after fees and slippage.
- **What worked**:
  - Count a win only above 1bp, and report breakevens as flat.
  - Permute bucket labels between clock-hour clusters, or between dates, inside each NY week. Bootstrap whole weeks for the confidence intervals.
  - Compare the bucket ranks of 2020-24 with those of 2025-26. They were anti-correlated (Spearman −0.07 to −0.43).
  - Choose the worst 3h window on 2020-24 alone, with a scan-corrected p, and test only that window on 2025-26. The chosen window failed in both families.
  - The one window that looks consistent, Beijing 21-24h (the US cash open), was found only by looking at both periods. It stays a forward-observation candidate, not a filter.
- **General rule**: For any calendar slicing (hour, weekday, news), (1) define the cluster unit before testing, (2) pick the bucket on an earlier period and confirm it on a later one, and (3) treat a pattern visible only in the pooled table as a hypothesis for forward data.
- **Related**: `yoyo/evaluation/monday_limit_time_news.py`, `experiments/active/exp-monday-limit-time-news-20261026-v1/`, [symbol-ranking-window-must-end-before-the-trading-window.md](symbol-ranking-window-must-end-before-the-trading-window.md).
