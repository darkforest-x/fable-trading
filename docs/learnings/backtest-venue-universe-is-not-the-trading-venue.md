# The backtest venue's coin list and wicks are not the trading venue's; check both before comparing results with paper

- **Problem**: The Monday 1618 research uses Binance USDT-M 5m bars for 626 alts. The paper engine trades OKX USDT swaps. A cross-check of the 2026 same-bar flash-wick trades against OKX candles was meant to test wick fills, and it turned up a bigger gap.
- **Dead ends**:
  - Assuming the paper universe matches the backtest. 60 of 102 checked 2026 trades are on coins with no OKX swap. For all v3 trades, only 263 of 829 in 2026 (and 138 of 537 in 2025) are on coins OKX lists today.
  - Assuming the same wick on both venues. The 2026-08-22 05:10 UTC crash was real on both, but OKX lows were shallower: 1INCH 0.08108 vs 0.07597, ACT 0.008381 vs 0.007652. Of 40 checkable same-bar trades, the OKX wick reached the entry in 33 and also closed beyond the target in 25.
- **What worked**:
  - Fetch the trading venue's public candles for the exact fill bars and compare them with the backtest's entry and target.
  - Use the venue's instrument list (OKX answers 51001 for unlisted IDs) to split all trades into tradable and not tradable.
  - On the listed coins, per-trade results were similar (2026 v3 153 vs 145bp). The venue mainly changes the number of trades, plus fills in flash wicks.
- **General rule**: Before comparing paper or live results with a backtest, intersect the backtest universe with the trading venue's listing and check wick parity on a sample of fills. Report results for the tradable subset. A full answer about another venue needs that venue's own history in the framework.
- **Related**: `yoyo/evaluation/monday_limit_okx_wick_check.py`, `experiments/active/exp-monday-limit-okx-wick-check-20261026-v1/`, [high-win-rate-subsets-can-be-same-bar-flash-wicks.md](high-win-rate-subsets-can-be-same-bar-flash-wicks.md).
