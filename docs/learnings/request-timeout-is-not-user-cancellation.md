# A timeout must not disappear as user cancellation

- **Problem**: Pro's API helper aborted timed-out requests with `AbortError`; the event wrapper intentionally ignored that error. Clicking could therefore end without either a result or a failure message. Rapid chart selections also allowed an older timeframe response to overwrite the newer selection.
- **Dead end**: Adding click handlers or changing button styling did not address request lifecycle semantics.
- **Effective path**: Distinguish timeout from explicit cancellation, expose timeout failures, suppress duplicate pending clicks, and route Enter through the same validated form action. Use generation checks across both symbol and timeframe changes. Add delayed-response and cancellation regression tests.
- **General rule**: Every asynchronous user action needs observable pending/success/error states; obsolete reads may be discarded, unknown writes must be reconciled rather than retried.
- **Files / boundaries**: `core.js`, `trading.js`, `ma-detection.js`, `app.js`, and Pro DOM interaction tests. Existing live execution confirmation and idempotency contracts stay in force.
