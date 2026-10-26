# Raising the win rate and raising profit are different levers; check the mean next to every win-rate gain

- **Problem**: The owner asked how to raise the win rate of the Monday 1618 limit-reclaim trades. The analysis started from 2,108 v3 trades and 22,708 v2 trades (existing framework fills, worse path, win = net > 1bp) and searched for entry-time conditions that raise the win rate.
- **Dead ends**:
  - Reading a higher win rate as a better rule. In v2 score 0.375-0.5, trades with a positive 15m EMA order score won 48-55 percent against 41-47 percent for the rest. They still lost money in 2025-26 (−100 / −43bp against +19 / +5bp), so their failed reclaims ran deeper.
  - Treating the target as fixed. For the same score ≥0.5 events, the near Monday edge wins 65-67 percent and the v3 far targets win 35-40 percent. The far targets still earn more per trade (176 vs 114bp late). The biggest win-rate lever is the exit, not a filter, and it costs money.
- **What worked**:
  - Each candidate was judged on win rate, flat share and mean net side by side.
  - Each bin was selected on 2020-24 and confirmed on 2025-26, with week-block bootstrap and Holm over 54 features.
  - The strongest lever was then checked inside every score band and every year. The 15m EMA order score (side-aligned, completed bars before the touch) stayed higher in each one. A positive value means price reached the level before the short EMAs turned.
  - Its timing was checked first, because the gain looked too good (+10 to +20pp): the last 15m bar used closes at or before the 5m fill bar opens.
- **General rule**: When asked to raise the win rate, report the mean (and later the equity replay) for the same subset. Then separate exit-driven changes (target distance, profit locks) from entry filters. Exit-driven changes trade win rate against payoff almost one for one.
- **Related**: `yoyo/evaluation/monday_limit_winrate_levers.py`, `experiments/active/exp-monday-limit-winrate-levers-20261026-v1/`, [best-hour-tables-need-select-then-confirm.md](best-hour-tables-need-select-then-confirm.md), [richer-score-features-raised-auc-not-top-band-profit.md](richer-score-features-raised-auc-not-top-band-profit.md).
