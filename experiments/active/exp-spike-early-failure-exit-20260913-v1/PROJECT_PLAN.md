# SPIKE V7 early-failure exit study — frozen execution contract

Owner authorized this isolated exit study on 2026-09-13. The four candidates
were fixed in `spike_early_failure_exit_study.py` before the full validation
aggregate was produced. This file was added after the completed run to make
that code-bound contract easy to audit; it is not represented as a pre-run
registration.

## Fixed inputs and split

- Input is the authenticated 3,531-stream V7 two-year replay, covering
  2024-09-10 through 2026-09-10 for 30m, 1H and 4H, both directions.
- Development is selected by `signal_bar_open < 2025-09-10T00:00:00Z`.
- The later year is authorized reused history and is explicitly nonblind.
- Because it extends beyond the project boundary of 2026-05-04, this is
  **holdout consumption #1 for this exact early-exit configuration**. Owner's
  instruction to execute all five studies and prior all-dates authorization are
  recorded in `holdout_usage.json`.
- Entry, initial stop, trailing stop, opposite-signal exit and 0.2% round-trip
  cost remain the frozen V6 execution contract. Only one early-exit rule changes
  in each arm.

## Fixed candidate arms

All decisions use completed bars and schedule a fill at the next bar open.

1. `no_new_extreme_2`: after two post-entry closes, exit if the directional
   high/low has not exceeded the original signal bar's directional extreme.
2. `no_new_extreme_3`: the same test after three post-entry closes.
3. `back_inside_rope_2`: during the first two post-entry closes, exit if price
   closes back inside the directional six-MA rope edge.
4. `back_inside_rope_3`: the same test during the first three closes.

## Development decision gate

A candidate must improve mean loss or mean per-stream closed-trade drawdown,
must not reduce mean per-stream account return at 1% risk, and must preserve at
least 95% of the baseline's realized 10R trades. If no candidate passes, the
result is rejection and validation is diagnostic only.

The same-entry month × asset sign-flip null is reported for changed exits. It
does not pretend that re-entry paths created by different exit timing are the
same trades.

No result changes production signals, Pine, notifications, orders, position
sizing or the frozen exit engine.
