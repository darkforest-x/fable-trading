# Derived chart links must invalidate when the period changes

- **Problem**: Monday's inspection link opened the patrol feed with a fixed 5m period. After correcting the destination, its row cache could still retain an earlier period even though the chart had changed.
- **Dead end**: Replacing the URL template alone does not fix cached HTML. A valid explicit contract also cannot be treated like a default suggestion merely because it is absent from the smaller inspection catalog.
- **Effective path**: Build the destination from the contract and current period, include the period in the row structure key, and retain valid explicit contracts. On a different selection, clear the previous chart and continuation state before awaiting the new catalog. Verify a real click plus period-change and delayed-load regressions.
- **Rule**: Trace every input of a derived navigation URL through its render cache and destination fallback. The URL, selected tab, contract and period must agree after asynchronous loading.
- **Scope**: `monday-fib.js`, `yolo-detect.js`, their frontend tests. Missing detection data remains explicit; navigation never submits a scan or an order.
