# Blow-up refills are cash events after the exit, not profit and not netted with the loss

- **Problem**: The allin3 paper account (all-in 3x) resets to a new 1000 USDT stake when a close leaves it below 100 USDT, as in the Langlang sizing replay. The analytics ledger rebuilds the equity curve as start equity + the sum of exit PnL.
- **Dead ends**:
  - Ignoring the refill. The curve stays at the ~95 USDT low while the account shows 1000, and `ledger_difference` stops being zero.
  - Folding the refill into the exit's timestamp. The curve merges events at the same millisecond, so -905 and +905 net to zero: the blow-up, its drawdown and its low disappear from the curve.
  - Counting the refill as PnL. That makes a blow-up look like a gain.
- **What worked**:
  - Refills are stored in their own table (`equity_before`, `equity_after`) and passed to analytics as cash events.
  - At the same time they are ordered after the exits, and the curve point is drawn 1 ms later. The drawdown is measured at the low, before the cash arrives.
  - Point PnL subtracts the cash added. `ledger_difference` includes the refills, so it stays 0.
  - Progress uses the backtest convention, equity / (1000 × stakes), so a leftover under 100 USDT counts as lost.
- **General rule**: Whenever an account can receive or lose cash outside trading (refills, withdrawals), keep it as a separate ledger event. Order it after the trades at the same time, and exclude it from PnL and win statistics.
- **Related**: `yoyo/monitor/monday_reclaim_paper.py` (`_refill`, `refills` table), `yoyo/monitor/reclaim_analytics.py`, `tests/monitor/test_monday_reclaim_paper.py::AllIn3Tests`, `yoyo/evaluation/monday_limit_langlang.py` (`path_run`).
