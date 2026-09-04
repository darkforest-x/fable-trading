# BTCUSDT.P independent 15m / 5m K1→K2 research

This experiment treats 15m and 5m as separate strategies rather than scaled
copies of the 1h script. The owner-authorized redesign keeps only the two
visual semantics as hard morphology rules: K1 must truly cross the selected
HL2 moving average and K2 must touch that average with its rejection wick
while its body remains on the directional side.

Secondary candle geometry, MA colour, and K1→K2 path continuity are combined
in a preregistered equal-weight score instead of independent vetoes. Each
timeframe selects its own reference-MA period, gap window, and score floor by
a single coordinate pass on 2023–2024 only. Entry, K2-extreme stop, 3R target,
12-hour horizon, 1.5R fee-cover protection, 20bp round-trip cost, and six-hour
cooldown remain frozen.

The 2025–2026-02 audit window is not a pristine validation set because prior
experiments already exposed its aggregate results. It is therefore labelled
an exploratory frozen audit. Repository holdout rows at or after 2026-05-04
must remain physically unread. No training, promotion, deployment, ACTIVE
mutation, forward-log mutation, or live order is in scope.

## Frozen outcome

Rejected on 2026-09-04. The independently selected settings increased audit
signal density from 53 to 181 events on 15m and from 74 to 418 events on 5m,
but mean net returns remained -25.00bp and -19.26bp per trade respectively.
Matched-control excess was statistically indistinguishable from zero, and the
twelve-component score had AUC below 0.5 on both timeframes. No Pine, ACTIVE,
frozen, deployment, or live-trading state was changed. See
`analysis/p1_btcusdtp_k1k2_15m_5m_independent_research_20260904.md`.
