# SPIKE V12.8 recent two-month descriptive backtest

Owner requested detailed signal count, win rate and profitable-excursion-to-stop
statistics, and confirmed both 15-minute and one-hour charts on 2026-09-23.
This is a frozen-rule measurement, with no tuning, training or production change.

## Frozen scope before replay

- Signal window: 2026-07-23 00:00 UTC through 2026-09-23 04:00 UTC (end exclusive).
  Earlier/later split: August 23. Warmup begins November 15, 2025, supplying more
  than 1500 four-hour candles before the window for long-lived HTF structures.
- Fixed universe: all 638 contracts in the existing Binance USD-M archive,
  including unavailable/delisted streams in the coverage ledger. This is not the
  current complete exchange listing and not a selection by recent performance.
- Reuse immutable five-minute files and verified recent cache; fill missing
  official monthly files and REST tails into a new research-only directory.
  Complete UTC buckets only; preserve gaps and record exclusions/censoring.
- V12.8 base entries/exits are unchanged from V12.6. Report admitted V9 signals
  (long and short) separately from long-only bk+spike joint signals. Report chart
  reference frames separately from executable next-open research trades.
- Executable trades retain original 20bp round-trip cost, initial five-bar
  extreme plus 0.2ATR / minimum 2ATR, close-2R activation and 4ATR trail, raw
  opposite-signal exits. No roll quantity or hypothetical add fill is invented.
- Denominators: candidate signals, taken serial trades, occupied skips,
  unavailable fills, closed trades and open/gap-censored trades all explicit.
  Net win means net return strictly positive among closed trades only.
- Floating-profit loss: closed net-negative trades, separate protective stops
  from all loss exits. Report established pre-exit MFE >0, >=0.5R, >=1R, >=2R,
  >=3R, >=5R and >=10R; distinguish gross-price excursion from fee-covering MFE.
  Stop-bar high/low cannot prove whether profit preceded the stop; report this
  ambiguity separately and do not use that extreme as established profit.
- Each direction/timeframe/signal family gets fixed-seed random entries matched
  by symbol, calendar week, earlier/later fold and causal ATR/close bin; same
  direction, exits and cost. No redraw on censoring. Compare bp as primary
  random excess to avoid risk-denominator bias. UTC week-block resampling and
  sign flips, with limited block resolution stated. No model, no AUC or score
  top-decile claim; V12.6 is the identical-rule baseline, not an independent arm.
- Required outputs: per-event/trade/control/frame/add-hint ledgers, coverage,
  15m/1h summary, long/short and monthly/weekly/BTC/ETH/symbol breakdown,
  MFE giveback bands and examples; source/config/data hashes and independent
  arithmetic review. Native TradingView event parity remains unverified.

## Verification

Commit builders and behavioral tests before market execution. Verify V12.8 to
V12.6 exact parent recovery, existing signal/exit reuse parity, future-prefix
causality, short-side symmetry, entry and stop-bar MFE semantics, hour timing,
and source/input identities. Do not weaken unrelated failing registry gates.
Report negative results and data/runtime failures. No parameter selection from
these two months; no promotion or account actions.

## Reproduction

The committed config fixes the cutoff rather than moving it during a long run.
Run `.venv/bin/python -m yoyo.evaluation.spike_v128_recent_data --workers 8` first.
Replay and summary commands are recorded in the final report once implemented.
