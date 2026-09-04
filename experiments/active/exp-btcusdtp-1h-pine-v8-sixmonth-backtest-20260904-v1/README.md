# BTCUSDT.P 1h Pine v8 six-month backtest

This experiment replays the exact current Pine v8 default `Core recall · 2–8`
signal on confirmed OKX `BTC-USDT-SWAP` one-hour candles. The owner explicitly
authorized reading prices at and after the repository holdout boundary on
2026-09-04. This is configuration-specific holdout use 1.

The contract is frozen in `config.json` before the outcome run. Entry is the
next bar open, the stop is the exact completed K2 extreme, the target is 3R,
the maximum path is 12 one-hour bars, same-bar TP/SL collisions resolve to SL,
and the unchanged round-trip cost is 0.2%. Every accepted signal remains in the
ledger; signals without a complete future path stay unresolved and do not enter
scored metrics.

The primary result treats every Pine signal as an opportunity, because the
indicator can draw overlapping zones. A separate one-position-at-a-time
sensitivity prevents those overlapping events from being mistaken for an
executable equity curve. Three matched random entries per resolved signal copy
direction, ATR risk, target, horizon, month, UTC time block and volatility
bucket.

The pre-entry diagnostic flags are frozen owner-morphology dimensions, not
holdout-selected rules. Their relationship with success/failure is exploratory;
no threshold may be changed and retested on this snapshot as if it were a fresh
confirmation.

## Reproduce after the preregistration commit

```bash
PYTHONPATH=. .venv/bin/python scripts/backtest_two_key_candle_pine_v8_btc_1h.py --fetch
PYTHONPATH=. .venv/bin/python scripts/validate_two_key_candle_pine_v8_btc_1h.py
PYTHONPATH=. .venv/bin/pytest -q tests/test_two_key_candle_pine_v8_btc_1h.py
python3 scripts/md_to_html.py analysis/p1_btcusdtp_1h_pine_v8_sixmonth_backtest_20260904.md --out-dir analysis/html
```

The fetch is isolated under this experiment's result directory and never
overwrites `data/kline_fetched/` or any live/forward file.
