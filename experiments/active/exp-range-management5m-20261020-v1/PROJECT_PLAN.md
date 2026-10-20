# Five-minute ATR management comparison

Owner request: change trailing management to completed 5m closes using 5m ATR, retain everything else, compare four coins with current 15m management (2026-10-20).

- The treatment is management timeframe: 5m close activation/ratcheting using Wilder ATR14 of completed 5m OHLC, versus existing 15m close activation/ratcheting using 15m ATR14. This paired management-clock intervention is explicitly requested; do not retune ATR length, multiplier, activation R, or signals.
- Freeze H1 watch, all subsequent same-direction completed 5m entries, initial last-five-5m structure stop, 5R activation, 4ATR distance, armed opposite H1 exit, no breakeven or risk cap, 20bp entry-notional cost, tick grids, candidates, random controls and yearly exit boundaries.
- Primary stratum is the current 0.05% buffer. The previously selected 0.2% buffer is a separate secondary paired stratum, never compared crosswise as if timeframe were the sole change.
- Reuse exact 2024-08-02 to 2026-09-22 14UTC candles/candidates from previous work. OKX BTC/ETH/PEPE, Binance ZEC. No new downloads or hashes. Prior 15m result ledgers remain read-only and are not replayed.
- Extend only the existing Nautilus config preparer with management_minutes (default15, allowed5/15), including this value in feature-cache identity. The native execution adapter already consumes explicit completed management events and remains unchanged. Use scripts/backtest-range for all eight new runs, two concurrent subprocesses maximum.
- Test default15 feature parity, actual 5m ATR14, prefix causality, cache invalidation, native off-quarter activation, next-bar stop effectiveness, and armed H1 reversal. Compare frozen entry/risk/fee fields to prior ledgers; audit only bounded distributed/extreme paths for the new semantics rather than repeating earlier full audits.
- Compare net R, gross R, mean R, win rate, 10R/20R retention, boundary marks and fixed-notional results, with same-time/side/volatility random controls. Report 2024/2025/2026 separately and later2025+combined. Nominal weekly block inference plus Holm across the eight frozen comparisons; no parameter search or untouched holdout claim.
- No Pine change, default upgrade, shared-capital return claim, training, promotion, browser or live-account action.

Reproduce in the installed isolated Nautilus environment:
`python -m yoyo.evaluation.range_management_study --phase run`
`python -m yoyo.evaluation.range_management_study --phase analyze`
