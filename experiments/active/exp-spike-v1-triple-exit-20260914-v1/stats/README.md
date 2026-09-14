# Triple-exit statistics

`yoyo.evaluation.spike_v1_triple_stats` reads completed stream ledgers and
writes compact CSV/JSON aggregates to `results/stats`. It does not build
signals, replay OHLC, choose controls, or generate the owner report.

`summary.csv` combines raw/dedup primary, diagnostic-arm, period, month, year,
timeframe, venue, and symbol rows. Only top20 dedup OOS timeframe rows carry
the preregistered nine-test excess p-values; all other p fields are `NaN`.
