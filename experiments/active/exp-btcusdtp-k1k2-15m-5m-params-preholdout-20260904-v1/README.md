# BTCUSDT.P 15m / 5m K1→K2 parameter optimization

This experiment selects two independent parameter sets. It uses development
rows from 2023–2024, freezes one coordinate-selected configuration per
timeframe, and opens the 2025–2026-02-28 validation ledger once. The repository
holdout beginning 2026-05-04 is physically outside both inputs and is not read.

The K2-extreme stop, 3R target, 12-hour horizon and 20bp round-trip cost remain
fixed because the owner did not authorize barrier or cost changes in this
request. No model training, promotion, deployment or order path is in scope.
