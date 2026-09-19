# BTC Parabolic RSI: fifth same-color entry / reverse strong-diamond exit

Status: specification pending owner clarification; no strategy returns scored.
Original observation: owner, 2026-09-20, Asia/Shanghai.
Notion research: https://app.notion.com/p/3e08856479af81169602db2278ae57d7

## Authorized scope

- New hypothesis, independently evaluated on OKX BTC-USDT-SWAP at 1h and 4h.
- Reuse the frozen three-year source from `exp-btc-rsi1h-sixma5m-20260920-v1`.
- Evaluation: 2023-09-19T21:00Z to 2026-09-19T21:00Z, exclusive end.
- Temporal descriptive split: 2025-09-19T21:00Z. No random time split or blind-holdout claim.
- Owner explicitly removes the initial price stop. Do not retain the old 3R target or six-MA filter.
- Same ChartPrime RSI14 and SAR .02/.02/.2; big diamonds compare SAR with 30/70.
- Preserve 0.2% round-trip entry-notional cost. No unapproved alternative cost/threshold search.
- Research only: no training, Pine release, production change, or live trading.

## Questions that must be resolved before scoring

1. Fifth same color: fifth visible SAR circle, fifth trend-state bar including the flip bar, or fifth same-color strong diamond?
2. Opposite big diamond exit: first event closes all, a specified staged schedule, or profitable-only staged exits? Must specify treatment of a losing position.

The source hides the SAR circle on every color-flip bar. Therefore fifth visible circle and fifth trend-state bar differ by one bar. A small diamond/ordinary flip is not a big diamond.

## Execution and evaluation design

- Use only complete UTC 1h/4h candles, with signals available at close and fills at the following bar open.
- Single serial position, no pyramiding, no queue of skipped entry events, unless owner clarification changes this.
- Preserve and separately value any terminal open position. An unclosed loss must not disappear from the evidence.
- No initial risk distance means R and stop-based risk sizing are undefined. Use entry-notional price returns, marked fixed-notional PnL, MAE/MFE and holding duration; do not label their sum an account return.
- Compare same asset/side/calendar month/causal volatility bucket random entries with the same confirmed exit policy and costs; retain terminal controls without outcome-dependent resampling.
- Report both directions, early/late/cross-boundary trades, monthly/yearly groups, uncertainty and matched random controls. No model scores exist, so AUC/top-decile selection is inapplicable.
- Previous six-MA/3R strategy is historical context only. Owner has requested a new bundled rule set, so changes cannot be attributed to one parameter.
- Commit final builder and specification before any strategy scoring. Keep immutable outputs and source/builder SHA receipts.

## Source identity

`experiments/active/exp-btc-rsi1h-sixma5m-20260920-v1/data/okx_btc_usdt_swap_5m.csv.gz`

SHA256: `076e9d1c74b9912a8d3421216fa6a10c3b19544fbdf93e90050dc92d2bf560ae`.

Source API documentation: https://app.okx.com/docs-v5/en/#rest-api-market-data-get-candlesticks-history

Funding, exchange mark-price liquidation and leverage-specific account risk are not covered by the existing OHLCV source. State this when interpreting the no-stop results.
