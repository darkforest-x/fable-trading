# BTCUSDT.P independent 15m / 5m sweep-reclaim entry experiment

This owner-authorized stage changes exactly one execution family: entry rule.
The baseline enters at the open immediately after K2. Alternative arms wait
15, 30, 60, or 120 clock minutes for a completed candle to sweep the K2
extreme and close back beyond both that extreme and the contemporaneous MA,
then enter at the next open.

The stop remains the original K2 extreme with zero ATR buffer. Signal
morphology, MA/gap/score parameters, 3R target, 12-hour horizon, 1.5R
fee-cover protection, six-hour cooldown, risk gates, and 20bp round-trip cost
remain frozen. Development is 2023-2024. Audit and the repository holdout stay
closed unless a timeframe passes every registered gate.
