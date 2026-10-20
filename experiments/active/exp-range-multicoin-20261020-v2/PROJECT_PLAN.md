# Add ZEC without retuning or replaying unchanged coins

Owner request: “加入 zec 币种” (2026-10-20).

- Preserve V1 outputs. Reuse its BTC/ETH/PEPE native configurations and cached results after checking the same study parameters; only ZEC executes new orders.
- Keep the common 2024-08-02 to 2026-09-22 14:00 UTC window, 2025/2026 year splits, baseline and BE3, 5-bar stop plus 0.05% buffer, 20bp cost and matched controls.
- OKX public instruments returns ZEC-USDT-SWAP listTime=1762398900578 (2025-11-06 03:15 UTC), tickSz=0.01, state=live. URL: https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId=ZEC-USDT-SWAP . Observation 2026-10-20. It cannot supply the original full period.
- Announced to owner before the run: use existing Binance ZEC native 5m archives for the common-period supplement and label the venue explicitly. Do not infer OKX performance or a pure symbol-only effect from this cross-venue comparison.
- Reuse two registered local data sources with exact overlapping OHLC agreement. Require all 225384 expected 5m candles including warmup, finite OHLC and tick alignment. Retain source metadata without content hashes; no new market-history download.
- No optimization, Pine change, trading execution, or production eligibility.

Reproduce:
`~/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.range_multicoin --config experiments/active/exp-range-multicoin-20261020-v2/config.json`
