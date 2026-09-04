# BTCUSDT.P 15m dynamic stop-manager experiment

This experiment freezes the predecessor two-stage K1-to-K2 signal and its 100
development entries. It changes one categorical factor only: the causal stop
manager. The baseline and five fixed candidate policies share the original
structure risk unit, 3R target, 48-bar horizon, 20bp round-trip cost and exact
matched-control design.

The candidate policies and their numerical constants are committed before any
new outcome is calculated. The comparison uses 2023--2024 chronological
half-year folds and a paired max-statistic sign-flip test across the policy
family. The 2025--2026-02 audit may open only after every development gate
passes. Repository holdout at or after 2026-05-04 remains excluded because the
owner has not authorized this new configuration to consume it.

This is research only. It cannot alter TradingView, ACTIVE, frozen, forward,
deployment, API keys or live orders.

## Final result

Rejected on development. Every policy and every chronological half-year had
negative mean net return. The best observed arm was `wick_r_ladder` at
-15.42bp per trade versus -16.09bp for the exact-parity baseline, a paired
improvement of only +0.66bp with familywise p=0.829. It rescued wick givebacks
but sacrificed almost the same contribution from original 3R winners.

The primary failure occurs before a profit stop can activate: 30/100 trades
stopped before +0.5R MFE and another 20/100 stopped before +1.5R. A post-entry
failure classifier using 20 causal features also failed expanding-window
validation (logistic AUC 0.412; depth-2 tree AUC 0.396). The audit and repository
holdout remained closed, and no TradingView or production state was changed.

Canonical report:
`analysis/p1_btcusdtp_k1k2_15m_dynamic_stop_preholdout_20260904.md`.
