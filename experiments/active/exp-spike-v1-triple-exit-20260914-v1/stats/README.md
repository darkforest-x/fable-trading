# Triple-exit statistics

`yoyo.evaluation.spike_v1_triple_stats` reads completed stream ledgers and
writes compact CSV/JSON aggregates to `results/stats`. It does not build
signals, replay OHLC, choose controls, or generate the owner report.

`summary.csv` combines raw/dedup primary, diagnostic-arm, period, month, year,
timeframe, venue, and symbol rows. Only top20 dedup OOS timeframe rows carry
the preregistered nine-test excess p-values; all other p fields are `NaN`.

Before aggregation, the reader writes `<cohort>_identity_dedup.csv`: a metadata
projection that canonicalizes only documented `1000`/`1000000` denomination
wrappers and reruns the fixed same-asset UTC-day selection. Source ledgers stay
unchanged. Controls inherit their native parent's identity decision. The paired
closed-baseline/triple reconciliation is in
`<cohort>_baseline_triple_closed_delta_summary.csv`; triple-only closures are
reported separately from same-event closed deltas.
