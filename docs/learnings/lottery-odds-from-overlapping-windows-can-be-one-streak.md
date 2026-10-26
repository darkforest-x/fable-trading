# Lottery-style odds from rolling windows can all come from one streak; inspect per-window outcomes before quoting them

- **Problem**: The owner wanted a version for a small stake, where blow-ups are acceptable but one run should multiply the stake tens to thousands of times. Monday 1618 v3 fills (score ≥0.6, opposite 1618 target) were replayed all-in at 3-20x leverage over 39 rolling 26-week windows starting each month, with a restart after each blow-up.
- **Dead ends**:
  - Reading the summary as odds. All-in 5x "reached 1000x in 10 percent of windows", which sounds repeatable. Per-window detail showed every 100x-1000x case came from the same 2025 streak: only the windows starting May to October 2025 contain it, and they overlap.
  - Reading the peak as a result. Held accounts gave the gains back: windows starting 2025-08 to 2026-01 ended near 0.3-1.6x with 9-18 blow-ups. The 2023-2024 windows peaked at only 1.4-25x.
- **What worked**: Report per-window medians next to the pooled odds, count blow-ups, and separate the peak (only reachable with a withdrawal rule) from the final account.
- **General rule**: With overlapping windows the independent sample is the number of distinct market regimes, not the number of windows. Before quoting "P(≥100x)", list which periods produce it, and model the profit-taking rule that would lock it in.
- **Related**: `yoyo/evaluation/monday_limit_lottery.py`, `experiments/active/exp-monday-limit-lottery-20261027-v1/`, [flash-wick-entries-need-room-and-leverage-picks-overfit.md](flash-wick-entries-need-room-and-leverage-picks-overfit.md).
