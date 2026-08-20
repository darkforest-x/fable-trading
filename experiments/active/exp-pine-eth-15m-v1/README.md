# ETH perpetual 15m Pine research v1

This directory freezes the first ETH-only 15-minute research contract derived
from the user-supplied ALLIN-V7.2 script.  It uses the bounded OKX
`ETH-USDT-SWAP` 15m series as the local research proxy and stops at
`2026-03-01T00:00:00Z`, 64 days before the repository holdout.

The selected V9 candidate keeps the V7 SMA10/SMA60 crossover, EMA100 regime,
ATR14 stop and oscillator construction.  It makes two alpha changes selected
in temporal development blocks: the existing project feature
`slow_slope_12` must agree with direction, and the oscillator threshold is
`0.1` instead of `0.2`.  Position risk defaults to 1% to reduce drawdown; that
is a sizing overlay, not evidence of stronger unit alpha.

After V9's final-preholdout period had already been inspected, a second
project feature (`vol_ratio_mean8 >= 1`) improved the historical point estimate
and drawdown.  That V10 result is recorded only as the next forward hypothesis:
it is post-selection, more tail-concentrated, and not independent OOS evidence.
V9 remains the last candidate whose two alpha changes were locked before its
single final-preholdout evaluation.

Important limitations:

- `ETHUSDT.P` is a TradingView display convention, not a venue identity.  The
  OKX cache is not assumed to be bar-identical to Binance, Bybit, or another
  TradingView perpetual feed.
- The 2025-01 through 2026-02 final-preholdout period has now been inspected.
  It is no longer an unseen OOS set for this strategy family.
- No TradingView compile/export parity has passed.  Python results remain a
  translation diagnostic, not deployable broker-emulator evidence.
- Matched controls use unique entry starts and split-contained exits.  Their
  return windows may overlap because multi-week trend holds otherwise make an
  exact same-regime control impossible; inference is therefore clustered by
  UTC week rather than pretending trades are independent.
- The existing project LightGBM model is not a valid Pine gate.  The exported
  feature table is explicitly training-ineligible while P0/P1 blocks training.
- Nothing here changes `models/ACTIVE`, creates `active_bundle.json`, promotes,
  deploys, writes forward logs, or touches a live account.

## Reproduce

```bash
PYTHONPATH=. .venv/bin/python scripts/research_pine_eth_15m.py
PYTHONPATH=. python3 scripts/reconcile_pine_eth_15m_backtesting.py
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_pine_allin_v7_backtest.py \
  tests/test_research_pine_eth_15m.py
PYTHONPATH=. .venv/bin/python scripts/build_pine_eth_15m_report.py
PYTHONPATH=. .venv/bin/python scripts/md_to_html.py \
  analysis/p0_pine_eth_15m_v1_20260821.md --out-dir analysis/html
```

The Docker recipe is an independent, read-only rerun surface:

```bash
docker build -t fable-pine-eth15m-v1 \
  experiments/active/exp-pine-eth-15m-v1/docker
docker run --rm --network none -v "$PWD:/workspace:ro" \
  -v "$PWD/experiments/active/exp-pine-eth-15m-v1/results-docker:/output" \
  fable-pine-eth15m-v1
```

The report builder writes the canonical Markdown analysis.  Project policy
then converts it to HTML for owner delivery.
