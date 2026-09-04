# BTCUSDT.P independent 15m / 5m profit-protection trigger experiment

This stage changes exactly one exit-management factor: the close-based R
threshold that arms the existing fee-cover stop on the next bar. Arms are
0.75, 1.00, 1.25, 1.50 (baseline), 2.00, 2.50R, and disabled.

Signal morphology, independently selected MA/gap/score parameters, immediate
next-open entry, original K2 stop, 3R target, 12-hour horizon, six-hour
cooldown, risk gates, and 20bp round-trip cost remain frozen. Development is
2023-2024; audit and holdout remain closed unless a timeframe clears every
registered gate.
