# SPIKE V8 entry-process study v1

Owner authorized this exact research on 2026-09-13.  It is an offline, pre-registered descriptive validation with no development-period selection, no optimization, and no promotion decision.  It neither changes V8 admission, exits, accounts, monitors, Pine, notifications nor orders.

## Frozen population and chronology

- Input is the completed 3,531-stream V8 replay: Binance, OKX and Gate, 30m/1H/4H; signal confirmations from 2024-09-10 through 2026-09-10.
- Development is 2024-09-10 to 2025-09-10; reused validation is 2025-09-10 to 2026-09-10.  All dates were explicitly authorized by Owner.  This configuration records holdout-era exposure #1, but the latter interval has already been studied and is nonblind descriptive evidence.
- Outcomes are the frozen full serial V8 replay outcomes.  A new label only partitions those existing entries; it does not claim the resulting figures simulate a dynamically changed account.

## Independent pre-registered labels

1. **H1 stale evidence / no close progress.** Reconstruct the exact causal V6 state-machine *shape* evidence bar and require every reconstructed long/short confirmation mask to equal the frozen cache mask bar-for-bar.  This anchor is not necessarily the bar whose later state-machine step satisfies the volume/advance envelope.  Two independent pre-registered H1 labels share its `>=2`-bar stale condition: (a) maximum directional *closing-price* progress from anchor close through confirmation is at most 0.25 anchor ATR (never continued); (b) directional progress at the confirmation close itself is at most 0.25 anchor ATR (it may have advanced then returned).  Both maximum and confirmation-close advance are retained in every evidence row.  No confirmation-after bar participates.  `>=2` and `<=0.25 ATR` are this round's hypothesis values, never estimated optima; the H1 variants are never conjoined with each other or H2.
2. **H2 failed volume breakout reversal setup.** Independently tag, rather than require globally, a final V8 confirmation whose opposite-side breakout occurred 2--12 bars earlier.  The source range is frozen from the 12 bars before that breakout; the breakout close must leave its boundary with causal `rv >= 1.5`; an intervening close must return inside; then the final V8 confirmation must close through the frozen opposite boundary.  The full range→breakout→failback→confirmation interval must be contiguous at that stream's timeframe, valid OHLC, and free of the frozen data-gap flag; rejected candidate windows are counted.  No post-confirmation bar participates.  The 2--12-bar window and `rv >= 1.5` are pre-registered hypothesis values, never estimated optima.

H1 reports retained versus removed entries.  H2 reports setup versus non-setup entries and cannot be presented as a replacement for all V8, especially if its sample is small.  The labels are never conjoined or optimized by a grid.

## Fixed scoring and required evidence

The inherited V8 trade contract remains next-open entry, five-bar structural stop plus 0.2 ATR and minimum 2 ATR risk, 2R arm / 4ATR trail, raw opposite V6 exits, and 0.2% round-trip cost.  A development entry whose complete exit is at or after the split is purged from development outcome scoring and counted separately.  Outputs include every V8 signal with causal anchor/breakout/failback/confirmation times and frozen range, net R, PF, average R, realized and MFE 10R counts, months, period/timeframe/direction strata, and deterministic same-stream/same-period/same-calendar-month/same-direction near-time pairs.  The latter are only within-label-pool descriptive comparators: they have no volatility bucket and are not a complete matched-random-market control.  Existing V8 matched random controls are retained as their own limited descriptive artifact because they lack a control exit timestamp; they are never used for development selection (which this study does not perform).  Failed candidates and unmatched pairs remain in the output.

`python3 -m yoyo.evaluation.spike_v8_entry_process_study --output experiments/active/exp-spike-v8-entry-process-20260913-v1/results/full_v1 --official`

The runner refuses official output until this plan, its config, and its Python source are committed cleanly at `HEAD`.  A bounded pre-commit smoke must write only under `/tmp`.
