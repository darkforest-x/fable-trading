# Delivery QA

- Independent Python historical replay; no native Pine VM or TradingView trade-export parity.
- 10 execution/data probes and 66 layer-boundary checks passed (76 total).
- 62 independent saved-ledger/control-stratum checks passed; two ranking/paired precision comparisons also passed.
- Initial matrix-multiply warnings were checked against independent summation; outcomes and paired p unchanged.
- Default CSV parser altered one tie-sensitive diagnostic p. round_trip precision exactly restored the original p; provisional summary_verified.json is retained, canonical summary_final.json supersedes it.
- Final HTML opened in Codex in-app browser at http://127.0.0.1:8788/p0_pine_allin_eth4h_20260907.html. Accessibility tables and desktop screenshot visually verified. No mobile QA claim.
- Source dataset is fixed-hash Binance public USD-M ETHUSDT, 2020 through April2026. 2020 warmup, no holdout, no source candle writes, no trading or production actions.
- Optional exchange question received no response at delivery; Binance is an explicit assumption.
- Reports and records distinguish closed trades from marked equity, gross from net, and nominal leverage from exchange margin.
