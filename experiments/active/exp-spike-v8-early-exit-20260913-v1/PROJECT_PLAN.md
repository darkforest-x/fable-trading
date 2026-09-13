# SPIKE V8 early-exit supplement — fixed dynamic replay

Owner requested this V8-specific replay on 2026-09-13 after the prior
V7-only early-failure artifact was found inapplicable.  This plan and
`config.json` freeze the candidate set before any full result is written.

## Scope and inputs

- Rebuild the V8 (`<=3 ATR` rope-distance) entry stream dynamically from the
  authenticated 3,531 Binance/OKX/Gate 30m, 1H and 4H caches.  It is not a
  filter over an old trade table.
- Input spans 2024-09-10 through 2026-09-10, both directions.  Development is
  `entry_time < 2025-09-10T00:00:00Z`; selection reads only a truncated prefix.
  The later year is authorized, reused, nonblind history and is **holdout-era
  use #1 for this exact configuration**, recorded in `holdout_usage.json`.
- The frozen `exp-spike-v8-noise-filter-20260913-v1/replay_v1` `arm=v8`
  ledger is a baseline parity oracle only.  The raw V6 opposite signal feed is
  retained when V8 rejects a new admission, so it may still close an open
  position.
- Entry remains next open; the original stop and 2R/4ATR trail remain; round
  trip cost remains 0.2%.  No Pine, production, notification, order, sizing,
  dependency, registry, or public execution engine is changed.

## Frozen arms and decision rule

The five arms are `baseline`, `no_new_extreme_2`, `no_new_extreme_3`,
`back_inside_rope_2`, and `back_inside_rope_3`.  The four candidates use the
existing close-confirmed/next-open state machine.  Protective gap and
intrabar stops take priority at the next open; a raw opposite V6 exit on the
same signal close supersedes a scheduled discretionary exit.

Selection is fixed before reading reused validation outcomes.  A candidate
must improve mean loss or mean independent-stream closed-trade drawdown, not
reduce mean independent-stream 1%-risk account return, and retain at least
95% of the baseline's *exact same-entry* realized 10R winners.  There is no
parameter search.  Account means always use the identical fixed 3,531-stream
pool: a stream with no closed trade is zero return/zero drawdown, and a
nonpositive per-trade 1%-risk factor ends that stream as bankrupt (-100%)
rather than allowing a negative NAV to recover.  These diagnostics are
separate per stream and do not represent a shared or real account.

The report will include net R, PF, win rate, direction/timeframe/month tables,
exact-winner retention and missed 10R cases, independent-stream account and
drawdown diagnostics, and same-entry month × asset sign-flip nulls reported
separately for development and reused validation.  A full-period row is
descriptive only.  Its finite-draw p-value is `(exceedances + 1) / (draws +
1)` and explains only paired original entries, never extra reentries caused by
a changed exit.

Existing V8 random controls are deliberately not repurposed: their artifact
does not identify exit-created reentry outcomes, so treating them as a control
would create a false entry-edge claim.
