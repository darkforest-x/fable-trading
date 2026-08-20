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

A development-only nested ablation shows where that improvement comes from.
SMA10/60 cross-only and cross-plus-EMA100 were negative after cost in every
2023/2024 half-year.  EMA200 slope12 created the first positive weighted
expectancy, oscillator direction reduced noise, and the 0.1 threshold was the
first stage positive in all four halves.  This is evidence for a sparse
trend-aligned crossover, not evidence that a strict moving-average-density
shape has been learned.

The volume gate was selected again in all three incremental prequential
feature replays, but the exact three-block sign-flip p-value is 0.125 and the
18-gate selection-adjusted max-stat p-value is 0.50.  It remains a useful
paper-forward hypothesis, not a proven optimization.

Important limitations:

- `ETHUSDT.P` is a TradingView display convention, not a venue identity.  The
  OKX cache is not assumed to be bar-identical to Binance, Bybit, or another
  TradingView perpetual feed.
- The 2025-01 through 2026-02 final-preholdout period has now been inspected.
  It is no longer an unseen OOS set for this strategy family.
- No TradingView compile/export parity has passed.  Python results remain a
  translation diagnostic, not deployable broker-emulator evidence.
- Ordered 3-minute replay on the same OKX feed reconstructs all 40,704 final
  15-minute bars exactly and reconciles all 110 V9 exits and prices.  That
  rejects hidden 15-minute aggregation optimism locally, but still is not
  TradingView venue parity.
- Matched controls use unique entry starts and split-contained exits.  Their
  return windows may overlap because multi-week trend holds otherwise make an
  exact same-regime control impossible; inference is therefore clustered by
  UTC week rather than pretending trades are independent.
- The existing project LightGBM model is not a valid Pine gate.  The exported
  feature table is explicitly training-ineligible while P0/P1 blocks training.
- Nothing here changes `models/ACTIVE`, creates `active_bundle.json`, promotes,
  deploys, writes forward logs, or touches a live account.
- An exploratory shell `tail` displayed two raw post-holdout 3-minute rows
  before the bounded loader was written.  They were never loaded, scored, or
  used in a strategy calculation.  The incident is retained in the report
  because this repository records any holdout look, including an accidental
  operational preview.

## Reproduce

```bash
PYTHONPATH=. .venv/bin/python scripts/research_pine_eth_15m.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_robustness.py
PYTHONPATH=. python3 scripts/reconcile_pine_eth_15m_backtesting.py
PYTHONPATH=. python3 scripts/reconcile_pine_eth_15m_intrabar.py
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_pine_allin_v7_backtest.py \
  tests/test_research_pine_eth_15m.py \
  tests/test_reconcile_pine_eth_15m_intrabar.py \
  tests/test_analyze_pine_eth_15m_robustness.py
PYTHONPATH=. /tmp/fable-pine-eval-venv/bin/python scripts/build_pine_eth_15m_report.py
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
