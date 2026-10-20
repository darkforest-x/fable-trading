# Signal ordinal and profitable-fill ordinal need separate counters

- **Problem**: Starting RSI scaleouts at the second diamond while allowing reductions only at fee-positive prices introduces two different event counts.
- **Dead end**: Dropping the first profitable event from a filtered list would skip the first executable reduction, even when a losing first diamond had already occurred. That implements a different rule.
- **Effective path**: Count confirmed opposite diamonds strictly after entry before the profitability gate. Skip the first event irrespective of profit, then apply the fee-positive condition. Only actual reductions consume the five original-size fifths. Mirror the order in the independent outcome overlay and test both profitable and losing first events.
- **Reusable rule**: Define whether an ordinal counts raw confirmed signals, eligible actions, submitted orders, or actual fills before implementing a delayed trigger. Keep those counters separate.
- **References**: `experiments/active/exp-eth-timebe-rsi-20261020-v2/PROJECT_PLAN.md`, `yoyo/layers/l3_backtest/nautilus_independent.py`, `tests/evaluation/test_range_rsi_scaleout.py`. The first diamond can still tighten an existing trailing stop; skipping a reduction must not skip risk management.
