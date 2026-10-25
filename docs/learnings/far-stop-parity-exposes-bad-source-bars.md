# A far-away stop with a parity check exposes bad source bars that close-based rules never notice

- **Problem**: In the Monday 1.618 limit-reclaim backstop study, the "no stop" config was simulated with a stop placed 50x away from entry, and its results were checked trade by trade against the existing no-stop ledgers. Two trades failed that check:
  - BABYUSDT, 2026-06-05 09:30 UTC: bar high 1.30 against a price of about 0.012 (OKX's high for the same bar was 0.01871).
  - DASHUSDT, 2020-04-14 07:35 UTC: bar low 0.10 against a price of about 73.

  Both spikes crossed even the 50x stop.
- **Dead ends**: The no-stop rule exits on the 60m confirmation close or on the target. It never looks at a single bar's extreme, so earlier studies ran on these bars without noticing anything. The first suspicion was an adapter bug in the new resting-stop path. Reading the bars and cross-checking another exchange's candle showed the adapter was correct; the data was not.
- **What worked**:
  - Keep the parity check. Any mismatch points at either a code bug or a data defect.
  - Read the bars around the mismatch and compare the bar with a second venue.
  - Drop the affected event from every variant, so all variants are compared on the same trades, and record the bar in the config.
- **General rule**: When a new rule starts reading intrabar extremes (stops, wick filters, MAE), first run a degenerate setting that must reproduce the old ledger exactly. Treat every mismatch as either a bug or a bad bar, and settle which one it is before reading any results.
- **Related**: `yoyo/evaluation/monday_limit_backstop_compare.py`, `monday_limit_v3_grid_compare.py`, `monday_limit_v3_ladder_compare.py` (BAD_EVENTS), [paper-engine-must-reuse-backtest-intrabar-conventions.md](paper-engine-must-reuse-backtest-intrabar-conventions.md).
