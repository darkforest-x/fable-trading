# BTCUSDT.P 15m / 5m K1→K2 parameter optimization v2

This clean successor replaces v1 after preflight discovered that v1's legacy
15m file was not physically pre-holdout. Both timeframes now originate from
one official OKX monthly-archive file that physically ends at
2026-02-28T15:55:00Z. The 15m input is causally aggregated from complete groups
of three 5m rows before any feature is calculated.

Development uses 2023–2024. One separately selected configuration per
timeframe is committed before 2025–2026-02-28 validation is opened. Stop,
target, horizon, protection and cost remain fixed. No training, promotion,
deployment or live order is in scope.
