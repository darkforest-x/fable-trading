# Synchronous entry protection can close before target submission

- **问题**：A new resting-entry/fixed-target combination failed when the entry bar also crossed the stop, including a true adverse opening gap.
- **死胡同**：Simply allowing the two already-supported order types assumed that submitting a stop returned before it could close the position. The native synchronous fill callback re-entered the strategy, leaving zero quantity for the subsequent target.
- **有效路径**：Protect the entry immediately, then recheck remaining exposure before submitting the target. Native matching still determines entry and stop timing; no losing entry is canceled or omitted.
- **通用规则**：After any synchronous order submission, treat position state as mutable through nested callbacks. Verify both normal continuation and immediate closure before submitting dependent orders.
- **牵连**：`yoyo/layers/l3_backtest/nautilus_independent.py`; `tests/evaluation/test_monday_fib_limits.py`; original limit/pivot behavior and true gap losses must remain unchanged.
