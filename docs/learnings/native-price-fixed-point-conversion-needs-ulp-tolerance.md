# Native price conversion needs ULP-aware equality

2026-10-24. The first Monday MA6 ETH replay stopped on a fill equality assertion: configured entry 3082.631858, native float 3082.6318579999997, difference 4.547473508864641e-13. This is one binary floating-point ULP, not a different tick (tick 1e-6). The market-entry path already allowed two ULPs; the resting-limit path did not.

Use max(tick-relative tolerance, two ULPs of each operand) for this equality assertion. Native matching, price construction, fee calculation and orders remain unchanged. Keep tick alignment and account-PnL reconciliation. A four-bar native regression reproduces the high-notional, fine-tick conversion. Do not round away meaningful price differences or widen trading thresholds.

Completed WIF output predates this assertion-only repair and remains valid; failed BTC/ETH runs produced no completed outcome ledger. Preserve each symbol's exact code stamps and do not rerun successful outcomes merely to unify source timestamps.
