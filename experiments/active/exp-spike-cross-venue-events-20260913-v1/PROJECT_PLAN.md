# SPIKE V7/V8 cross-venue event study — preregistered reused-history analysis

Owner authorized this standalone, read-only study on 2026-09-13. It may read
only the completed `exp-spike-v8-noise-filter-20260913-v1/replay_v1/streams`
receipts. The 2024-09-10 through 2026-09-10 population, including the
development/validation split, has already been inspected. This is descriptive
nonblind reuse, not a fresh holdout result. There is no data fetch, monitor,
notification, execution, Pine, model, threshold, promotion or order change.

The source extends beyond the project holdout boundary of 2026-05-04. Owner's
instruction to execute all five studies, together with the prior explicit
all-dates authorization, records **holdout consumption #1 for this exact
cross-venue configuration**. `holdout_usage.json` preserves that receipt. The
read is nonblind and grants no production status.

## Fixed unit and rule

- Arms: V7 and its frozen V8 subset; both directions and 30m/1H/4H.
- Canonical event key: `asset + timeframe_min + side`. Venue, venue-specific
  symbol and stream are deliberately excluded from the event identity.
- Sort source confirmations by their closed-bar availability timestamp. The
  earliest confirmation is the leader. A group can include rows only through
  `leader + one timeframe`; the group never chains across a second bar.
- A group is multi-venue confirmed only when a second *distinct* venue first
  appears within that one-bar deadline. Multiple streams/rows from one venue do
  not increase `venue_count`; all such rows remain traceable in the membership
  artifact.
- The primary folding and all outcome tables include every V7/V8 source
  admission. An observed-V7-admission `asset/timeframe/period/calendar-month`
  two-venue field is output only as a sensitivity analysis. It is retrospective
  within the month, so it cannot be a causal gate or an entry rule; it is not
  proof that both exchanges had continuous market-data coverage in that month.

## Outcomes and reference

Existing serial-replay trades are joined only by the exact
`stream_key + signal_bar_open + side + arm` identity. Missing exact trades are
reported as `occupied_or_unmapped` admissions, never as losses. Censored trades
remain in their own denominator and are excluded from win rate, PF, mean net R
and realized-10R calculations. A duplicate or trade with no source signal is a
hard error.

The frozen replay's matched controls use same stream, side, calendar month and
causal prior-volatility bucket, with the same exit engine and 0.2% cost. They
are reported for any selected source rows. Their sparse, one-target-per-
stream/arm/side/period construction means they are an event-level null check,
not a portfolio control; if selection leaves none, the output says so instead
of substituting a favorable comparison.

## Deliverables and decision boundary

The runner emits immutable-style CSV/JSON evidence for all-source memberships,
folded events, delay distribution, V7/V8 baseline and confirmed subsets, the
optional retrospective coverage sensitivity, and the matched-control deltas.
It records all source identities and input receipt counts. The study does not
simulate entering after the later confirmation: the
trade summaries describe original venue-specific entries tagged by later
cross-venue evidence. Therefore they cannot establish a tradable delayed-
confirmation edge. No result authorizes production use; forward shadow samples
remain required.
