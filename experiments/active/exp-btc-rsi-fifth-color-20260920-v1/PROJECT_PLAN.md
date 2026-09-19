# BTC Parabolic RSI: fifth same-color entry / reverse strong-diamond exit

Status: owner clarified entry and scale-out; specification frozen before scoring.
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

## Owner clarifications and frozen execution

1. Owner: "就是那个大菱形啊". Only strong diamonds count. Ignore circles and ordinary flips; a different-color strong diamond resets the run to one.
2. Enter on exactly the fifth consecutive equal-color strong diamond, long for bullish/up, short for bearish/down. A sixth or later event does not trigger another entry in that run.
3. Owner: "出现不同颜色的大菱形就平25%" and explicitly "按最初仓位的25%，四次反向大菱形后全部平仓".
4. Each opposite-to-position strong diamond closes one quarter of original quantity. Same-side strong diamonds do not undo prior reductions or reset the exit count. Four opposite events fully close; they need not be consecutive.
5. Execute reductions even at a loss, as communicated to owner. No profitability filter, stop, target, pyramiding, simultaneous hedge, or queued skipped entry. A position blocks later entry candidates; at an event process an eligible exit before considering a flat entry.
6. Both entry and reduction signals must be closed 1h/4h candles. Fill at the following candle open (same timestamp as signal close), using the frozen five-minute opening price. An event at the exclusive study end cannot fill.
7. Start flat at START; pre-START observations seed indicator and large-diamond run counts. Evaluate events by their close/availability timestamp, so a complete 4h candle crossing START can legitimately become actionable at its later close.

The earlier circle-versus-state ambiguity is resolved in favor of neither: only the large-diamond event stream controls this strategy.

## Execution and evaluation design

- Use only complete UTC 1h/4h candles, with signals available at close and fills at the following bar open.
- Single serial position, no pyramiding, no queue of skipped entry events, unless owner clarification changes this.
- Preserve and separately value any terminal open position. An unclosed loss must not disappear from the evidence.
- No initial risk distance means R and stop-based risk sizing are undefined. Use entry-notional price returns, marked fixed-notional PnL, MAE/MFE and holding duration; do not label their sum an account return.
- Compare same asset/side/calendar month/causal volatility bucket random entries with the same confirmed exit policy and costs; retain terminal controls without outcome-dependent resampling.
- Report both directions, early/late/cross-boundary trades, monthly/yearly groups, uncertainty and matched random controls. No model scores exist, so AUC/top-decile selection is inapplicable.
- Previous six-MA/3R strategy is historical context only. Owner has requested a new bundled rule set, so changes cannot be attributed to one parameter.
- Commit final builder and specification before any strategy scoring. Keep immutable outputs and source/builder SHA receipts.
- Every entry has normalized original notional 1; quantity is 1/entry price. Cost is 0.001 on entry plus 0.001 times original quantity fraction on each reduction, totaling the unchanged0.002 over a completed lifecycle. Terminal marks reserve the remaining hypothetical closing cost separately from actual paid costs.
- Matched random entries use the same four-event exit policy and cost; no forced actual liquidation at the temporal split or END. Price paths beyond a reported phase boundary must not enter that phase's marked comparison.
- Before scoring, extend the indicator prefix from30 to90 days using the same OKX venue. Keep every original source row identical. Audit30/90 signal parity and native4h OHLC samples; this is pre-scoring data preparation, not parameter optimization.

## Source identity

`experiments/active/exp-btc-rsi1h-sixma5m-20260920-v1/data/okx_btc_usdt_swap_5m.csv.gz`

SHA256: `076e9d1c74b9912a8d3421216fa6a10c3b19544fbdf93e90050dc92d2bf560ae`.

Source API documentation: https://app.okx.com/docs-v5/en/#rest-api-market-data-get-candlesticks-history

Funding, exchange mark-price liquidation and leverage-specific account risk are not covered by the existing OHLCV source. State this when interpreting the no-stop results.
