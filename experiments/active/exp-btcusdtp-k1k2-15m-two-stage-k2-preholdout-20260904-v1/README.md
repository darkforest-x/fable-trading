# BTCUSDT.P 15m two-stage K2 pre-holdout experiment

This experiment changes one signal representation: K2 may be a causal
touch-to-confirmation interval instead of requiring touch and strong rejection
on the same candle. All K1 thresholds, SMA40(HL2), gap, execution barriers,
cost, cooldown, horizon and protection remain frozen from the 15m research
baseline.

The candidate definition is fixed before outcomes are calculated. Development
uses 2023--2024 only. The 2025--2026-02-28 audit can open only if every
development gate passes. Repository holdout at or after 2026-05-04 is excluded.

Freqtrade 2026.8 runs as a full, Docker-isolated second implementation using
the exact same locally materialized OHLCV source. It does not place live or
dry-run orders.
