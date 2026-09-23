# SPIKE V12.8: two structural add hints

Owner asked to add the previous two-add roll to V12.6, then explicitly selected
chart hints with the original exits retained on 2026-09-23. This is a presentation
and causal-state delivery, not an economic experiment or account execution.

## Frozen scope

- Derive a new V12.8 source from V12.6. Preserve V12.6 and the separate V12.7
  held-break-age candidate. Do not incorporate V12.7's behavioral changes.
- Bind hints to the original long reference frame. At most two *candidates* per
  frame; the count is not a count of accepted fills.
- Use completed native H1 OHLC. On lower minute charts that divide one hour,
  use offset H1 tuples and emit only at the confirmed chart bar where the tuple
  becomes available. Do not backdate a hint to the H1 close or use a partially
  pre-entry hour. Unsupported charts have an explicit inactive status.
- Freeze the original entry and initial stop for the 2R threshold. An H1
  pullback freezes the preceding running high; a later close above it consumes
  one structural opportunity. The pullback low minus one tick is a proposed
  structure reference, which must strictly rise. Both candidates use 2R, not
  the abandoned V0.1 second-stage 4R rule.
- The structure reference continues to update after candidate two. If a prior
  reference is subsequently touched, pause hints until the next original long
  frame. This never closes or changes the original frame.
- No quantities, profit-retention admission, margin, mark-price liquidation,
  funding or accepted-fill assertions. No actual orders, notification setup,
  training, promotion or cost/stop-parameter changes.
- Minimal display hides the new visuals without changing the hint state.

## Acceptance

1. Removing marked V128 blocks and reversing version-only replacements must
   recover the immutable V12.6 SHA256
   `8a72c18b65e50862ca6dee4e4d58cac4c1e1c7d38ccd3718e7f0228e970ee2db`.
2. New state can write only V128-owned state and objects. Original entries,
   stops, reverse exits, early exits and alert conditions are unchanged.
3. Check complete-hour admission, repeated-tuple deduplication, 2R threshold,
   two-candidate cap, post-cap reference updates, gap invalidation, resets and
   effective-next-bar reference handling.
4. Compile a new private TradingView script and check the supported chart
   surface, settings and original/new plot budget. Record any unverified case
   rather than calling static tests a native event-level parity test.

The zero-change control is the full-parent byte recovery, not a profitability
comparison. AUC, permutation p, returns, win rate and matched random entries do
not apply because no trading behavior or economic performance is evaluated.

## Sources

- `docs/profitable_roll_v04.md`
- `yoyo/evaluation/profitable_roll_v03.py`, structural clock and accounting scope
- `analysis/p1_profitable_roll_v03_20260921.md`, including negative results
- TradingView Pine v6 [confirmed HTF values](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/#avoiding-repainting)
- [Bar confirmation](https://www.tradingview.com/pine-script-docs/concepts/bar-states/#barstateisconfirmed)
- [Plot limits](https://www.tradingview.com/pine-script-docs/writing/limitations/#plot-limits)
