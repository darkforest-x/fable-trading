# BTCUSDT.P independent 15m / 5m partial-runner experiment

This stage changes exactly one position-management factor: the fraction kept
as an 8R runner after price first reaches 3R. Registered runner fractions are
0%, 10%, 25%, 50%, 75%, and 100%; the remainder exits at 3R.

Signal morphology, next-open entry, original K2 stop, 1.5R close-based
fee-cover protection, 12-hour horizon, six-hour cooldown, risk gates, and one
20bp position-weighted round-trip cost remain frozen. Development is
2023-2024. Audit and holdout remain closed unless every gate is cleared.
