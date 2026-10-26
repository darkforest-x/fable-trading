# A very high win-rate subset can be made of same-bar flash wicks; check holding time before trusting it

- **Problem**: In the Monday 1618 limit-reclaim fills, near target with score ≥0.6 and a positive 15m EMA order score won 89.5 / 77.5 percent (2020-24 / 2025-26, n 143 / 306). The owner asked what it is and wanted sampled charts.
- **Dead ends**:
  - Reading the win rate as a better entry. The median holding time was 5 minutes in 2020-24 and 0 in 2025-26. Half of the 449 trades (224) fill and hit the target inside the same 5m bar: a wick through the 1618 that closes back beyond the Monday edge. These trades are 100 percent winners by construction under the worse intrabar path, because that path only lets a same-bar target count when the bar closes beyond it.
  - Treating the subset as profitable without that half. The other 225 won 62.7 percent at +32bp, and in 2025-26 50.7 percent at −90bp.
  - Using it as a filter on v3. Keeping only order score > 0 trades lowered the 1000U multiple in every period (2025: 3.59 vs 9.31x), because the dropped trades were still profitable on average.
- **What worked**:
  - Split the subset by holding time (same bar vs later).
  - Draw sampled trades, including the worst one (−42.5 percent with no backstop).
  - Replay equity with exposure controls: doubling the order > 0 trades beat doubling the order = 0 trades (last five months 5.19x DD 19 vs 3.25x DD 32), but was close to plain doubling overall. The order score is better used as a sizing tilt than as a filter.
- **General rule**: Before believing a high win rate from bar data, look at the holding-time distribution. Same-bar fills and exits mean the result depends on wick fills at the source exchange (here Binance 5m, while trading would be on OKX). Cross-check those wicks on the trading venue and treat them as an execution risk, not as edge.
- **Related**: `yoyo/evaluation/monday_limit_near_order_samples.py`, `yoyo/evaluation/monday_limit_order_replay.py`, [win-rate-levers-and-profit-levers-are-different.md](win-rate-levers-and-profit-levers-are-different.md), [paper-engine-must-reuse-backtest-intrabar-conventions.md](paper-engine-must-reuse-backtest-intrabar-conventions.md).
