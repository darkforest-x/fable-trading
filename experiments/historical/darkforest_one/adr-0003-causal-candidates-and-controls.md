# ADR 0003: Causal candidates and offline matched controls

- Status: accepted
- Date: 2026-08-03

## Context

The frozen protocol named normalized MA density and a volatility percentile but did not yet define
the percentile window, tie behavior, feature warm-up, or control-selection contract. Leaving those
choices implicit would allow incompatible P1 datasets to claim the same protocol lineage.

## Decision

Candidate features require all SMA/EMA 20/60/120 values and ATR14 to be finite. Volatility is
`ATR14 / close`; its percentile is the weak empirical rank of the current value within a full
2,880-observation trailing window, including the current closed bar. Candidate `signal_time` is
the bar's `available_time`.

Controls are a separate offline evaluation cohort. Each candidate receives one control that is
low-volatility but fails the MA-bandwidth threshold, matched exactly on UTC month, four-hour block,
weekday/weekend, volatility decile, and price-distance sign. Selection uses a fixed SHA-256 score.
Controls may be reused across candidates. No label or later price path participates in matching.

Candidate rows, control rows, diagnostics, configuration, canonical lineage, and source commits
are bound by semantic and file hashes in a fail-closed manifest.

## Consequences

- Appending future bars cannot change historical point-in-time features or candidate membership.
- Controls can occur after their candidate because they are research comparators, not signals.
- A completed historical month has a stable matching pool; the current partial month can change
  when the frozen canonical snapshot advances, and the new manifest records that change.
- Exact matching can fail when a stratum has no eligible wide-band control. The build aborts rather
  than silently relaxing the protocol.
- A legitimate zero-candidate snapshot is persisted with zero rows and JSON-safe diagnostics.
