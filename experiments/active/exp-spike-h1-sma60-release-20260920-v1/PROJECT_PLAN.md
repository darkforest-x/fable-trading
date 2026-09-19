# SPIKE V12.3 / V9.1: owner-selected 15m H1 SMA60

Owner explicitly selected SMA60 for 15m only on 2026-09-20 after viewing the prior grid. This is an authorized release choice, not a new claim of out-of-sample optimality.

- Independent files and private TradingView scripts; preserve V12.2 and V9.
- Compare signal close strictly above/below the previous completed native H1 SMA60; equality or unknown blocks admission. Confirmed-H1 timing matches the existing study's chart-open alignment.
- Default enable; non-15m and disabled mode bypass the new gate. Keep raw opposite confirmations available to exits. Preserve stop, cost, state machine, line detection, and pairing logic; fewer admitted V9 frames can change downstream joint signals.
- Complete source-preservation contracts, existing causal/serial HTF tests, Luna Max review, and native compile plus 15m/1h settings verification for both new scripts.
- Native hourly data cannot prove the study's complete-5m-bucket equivalence; no full native trade parity or new return test is claimed.
- No deployment, alerts creation, orders, model training, HTML, or old-file edits. Commit code before native runs.

Reference: https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/ — prior HTF expression offset plus lookahead_on.
