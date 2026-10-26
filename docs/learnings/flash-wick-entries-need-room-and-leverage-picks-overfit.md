# Spike entries need backstop room, and the maximum-return leverage setting picked on early years fails later

- **Problem**: The owner wanted a smaller backstop than −30 percent, plus slots and leverage under a 40 percent account drawdown, for the spike subset of the Monday 1618 limit-reclaim rule (score ≥0.6, 15m EMA order > 0; 449 trades on Binance 5m).
- **Dead ends**:
  - Tight backstops. Spike fills overshoot the 1618 before rebounding. Winners' floating loss before confirmation had a median of 2-4 percent, a 75th percentile of 5-8 and a 90th of 10-14. Framework fills with 3 / 5 / 7 percent stops cut the 2026 near-target win rate from 81 to 35 / 50 / 60 percent and turned the mean per trade negative. Fifteen to twenty percent is the smallest stop that keeps about 75 percent winners.
  - Choosing slots and leverage by the best 2020-24 multiple under the drawdown cap. The winner (range 0.5 target, 20 percent stop, 3 slots, 5x) made 16.7x there, then 0.78x with a 63 percent drawdown in 2026. Few slots with high leverage concentrate cluster-day risk (2026-08-22, 2026-01-19).
- **What worked**:
  - Read the MAE of winners before setting a stop.
  - Test candidate stops on framework fills, not on MAE arithmetic.
  - For leverage, keep the settings that stay under the drawdown limit in every period with margin to spare, for example 10 slots at 5x or 5 slots at 3x. Report liquidations and skipped fills next to the multiple.
- **General rule**: A stop tighter than the winners' typical adverse excursion changes the strategy rather than refining it. A leverage setting chosen at the edge of a drawdown limit in one period is likely to break it in the next, so choose for robustness across periods, not for the peak.
- **Related**: `yoyo/evaluation/monday_limit_spike_opt.py`, `experiments/active/exp-monday-limit-spike-opt-20261026-v1/`, `experiments/active/exp-monday-limit-spike-stops-20261026-v1/`, [high-win-rate-subsets-can-be-same-bar-flash-wicks.md](high-win-rate-subsets-can-be-same-bar-flash-wicks.md).
