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
