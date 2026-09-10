# SPIKE Burst V1 — two-year all-market replay

Frozen before collection on 2026-09-11 (Asia/Shanghai): evaluate the two UTC
years `[2024-09-11T00:00:00Z, 2026-09-11T00:00:00Z)`, with a 376-day causal
warm-up fetch beginning `2023-08-31T00:00:00Z`.  That is required for V1's
340-bar daily SMMA/MA stabilization; new listings and post-gap daily segments
that cannot meet it are explicit warmup exclusions.  The right endpoint is the end of the latest fully closed UTC
day (2026-09-10); no 2026-09-11 candle may enter this run.

The contract is the unchanged `yoyo/evaluation/pine/spike_burst_v1.pine`
(SHA `18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2`).
Defaults are long-only.  It emits after the signal bar closes.  Executable
trades enter at the next bar open; the separate signal-close reference is
retained only as a diagnostic.  The frozen protection from the Pine signal bar
is active after entry.  A next-open gap through it is charged at that open;
when both stop and target-like extrema could occur in one later candle, the
stop is assumed first.  No fixed take profit exists.  Round-trip cost is the
existing 20bp (10bp at entry and exit), with funding and market impact not
modelled.  End-of-window positions are explicitly censored/marked, never
called natural exits.

Universe is every *currently returned* linear USDT perpetual in the frozen
Binance USD-M, OKX SWAP and Gate USDT-futures catalogs, regardless of apparent
return.  This is an all-covered-current-catalog universe, **not** all-ever-
listed: removed listings, historical metadata and funding history are not
recoverable from those catalog snapshots.  Each catalog row and every missing
page has a receipt/error ledger.  No gap is filled; continuous segments restart
the Pine warmup.  Four requested timeframes (30m/1H/4H/1D) are complete UTC
aggregations of confirmed 30m source candles.

Candidates, their entry-time features, and deterministic matched controls are
saved before exit simulation.  Controls use the same venue/symbol, calendar
week, and prior 20-bar ATR percentile bucket and exclude ±12 bars around any
signal.  Fixed seed and matching policy make this an observational benchmark,
not a causal randomized claim.  The run consumes the explicitly authorized
holdout once for this frozen configuration; it performs no training, selection,
or parameter changes.
