# V2.21 post-arrow confirmation bars

Owner request (2026-10-21): reuse V2.21 data and costs, change only the
post-arrow confirmation count (0/1/2), keep the confirmed-pivot exit, compare
net R and mean net bp against same-coin same-week matched random controls.

Single variable: `after` in the V2.21 reference matcher. post2 is the frozen
default and must reproduce the prior comparison candidates and pivot ledger.
Execution reuses the NautilusTrader runner (`scripts/backtest-range`).
Holm correction covers the three variants per symbol.

No new data fetch, Pine edit, report, model, default or live trading change.
