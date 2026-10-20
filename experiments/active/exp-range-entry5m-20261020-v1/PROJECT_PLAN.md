# Five-minute entry confirmation, four symbols

Owner request: “测试一下 5min 级别”, confirmed “按这个理解，四个币都测” (2026-10-20).

- Change entry confirmation only: H1 confirmed watch -> every strictly later same-direction completed 5m Stoch(5,3,3) arrow.
- Keep H1 watch refresh on every arrow; simultaneous H1/lower signal is not later. Allow independent overlapping entries.
- Keep the last five completed 5m bars plus 0.05% entry-price stop buffer, stop grids, 20bp entry-notional cost, year boundaries and prior data.
- Keep management at 15m closes: baseline arms at 5R, ratchets 4ATR14 and exits on later/opportune opposite H1 only when armed. BE3 adds the already-frozen 15m-close 3R cost breakeven.
- Candidate matching uses the latest completed 15m ATR at each 5m close. Forward-fill for candidate selection only; the native execution adapter still receives sparse 15m ATR update events. Never backfill.
- Reuse exact prior continuous 5m OHLC inputs. BTC/ETH/PEPE are OKX; ZEC is Binance as previously disclosed. Period 2024-08-02 through 2026-09-22 14:00 UTC, warmup from August 1; splits January 1, 2025/2026.
- Recompute causal candidates and same-symbol/side/week/segment/volatility-bucket controls. Compare previous 15m results directly without replay. Report both total R and mean R because trade counts change.
- No parameter optimization, new data download, live/Pine change, account-return interpretation or training.

Reproduce using the installed isolated runtime:
`~/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.range_multicoin --config experiments/active/exp-range-entry5m-20261020-v1/config.json`
