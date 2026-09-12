# A positive float is not a tradable risk distance

- **Problem**: A matched random-entry diagnostic reported billions of negative R even though its ordinary net returns were finite.
- **Dead end**: Finite-value checks and the condition `risk > 0` accepted a short entry0.00441 and stop0.004410000000000001. Dividing transaction cost by that one-ULP distance produced about minus21.7 trillion R. Averaging the values did not make them informative.
- **Effective path**: Trace the largest R back to entry, stop and tick. The two duplicated control matches were numerically zero-risk; the actual20,184-row ledger had at least7 ticks of risk and was unaffected. Retain raw diagnostics, exclude the disclosed floating-zero-risk matches using machine precision, and report the already-recorded unit-notional returns with a separate month-block null. No sample was redrawn or strategy threshold fitted.
- **General rule**: Validate the economic denominator before interpreting ratios. A finite ratio is not sufficient; distinguish floating-point zero, sub-tick invalidity, and valid but economically tiny risk. Changes made after seeing an anomaly must be disclosed as diagnostic, not blind validation.
- **Related files**: `yoyo/evaluation/spike_v1_plus_controls.py`, `yoyo/evaluation/spike_v1_plus_delivery.py`, `analysis/p1_spike_v1_plus_backtest_20260912.md`.
