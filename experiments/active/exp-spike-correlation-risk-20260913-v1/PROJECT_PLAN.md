# SPIKE causal 30-day correlation portfolio-risk study

This authorized, read-only, nonblind reused-history study compares the formal
cross-venue event leaders under three fixed portfolio-admission policies:

1. event leaders with no portfolio cap;
2. event leaders with at most five open positions; and
3. policy 2 plus a rejection when the candidate's largest trailing 30-calendar-
   day return correlation with an already open *different* asset is at least
   0.80.

The availability clock is explicit: an OHLC bar indexed at `t` closes at
`t + 30m`; its close-to-close return is eligible only if that close is strictly
before the frozen candidate `entry_time`.  The window contains exactly 1,440
unfilled 30-minute returns ending no later than `entry_time - 30m`.  A missing
source, bar, non-finite price/return, constant series, or incomplete window is
`correlation_history_unavailable` and is rejected by policy 3.  This is a
deliberately fail-closed data-quality condition.  Capacity releases at an exit
timestamp before an entry at that timestamp; same-entry-clock candidates use
the stable formal `arm:source_event_id` order.

For each underlying asset, the return source is selected independently of
signals and outcomes: use a 30-minute cached stream from Binance, then OKX,
then Gate; within a venue use lexicographically smallest stream key.  No
resampling, venue switching by date, missing-data fill, or outcome-dependent
source choice is allowed.  The complete selection is emitted as evidence.

The formal event ledger supplies the event membership and frozen V7/V8
outcomes.  The authenticated V3 stream trades supply only exact frozen entry
and exit clocks plus already frozen price/risk fields.  A formally executed
row without a matching V3 clock is rejected rather than inferred.  The runner
requires matching V3 and formal `net_r`, `net_return`, and censored status when
a clock is present; admission code never reads those outcomes.

`event_leaders_max_open_5` is the no-op risk-policy null for the correlation
action.  Its comparison with policy 3 describes capacity/admission effects
only; it is not an alpha test or a production recommendation.  Metrics retain
development and authorized reused historical validation separately, and label
the `availability_time >= 2026-05-04` slice as this configuration's authorized
holdout use #1.  No live service, threshold, model, execution, or data source
is changed.
