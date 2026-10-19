# Pine ALLIN-V7 pre-holdout research

This directory is the isolated research home for the user-supplied
`ALLIN-V7.2` Pine strategy. It is not a production strategy bundle and it does
not change `models/ACTIVE`, forward logs, thresholds, barrier parameters, or
execution settings.

## Contents

- `config.json`: frozen replay contract and single-variable arms.
- `pine/allin_v8_research.pine`: mechanically safer Pine v6 rewrite.
- `results/`: generated pre-holdout outputs (created by the runner).
- `notebooks/`: generated, executed audit companion.

The original attachment is identified by SHA-256 in `config.json`; its 4x ATR,
3% hard cap, oscillator thresholds, and 0.2% round-trip project cost are kept.
The optimized Pine fixes calculation/execution semantics and makes the unused
risk sizing option real. It is research-only until TradingView export parity
and fresh forward evidence exist.

## Reproduce

```bash
PYTHONPATH=. .venv/bin/python scripts/backtest_pine_allin_v7.py
PYTHONPATH=. .venv/bin/python scripts/validate_pine_allin_v7_backtest.py
PYTHONPATH=. /tmp/fable-pine-eval-venv/bin/python scripts/build_pine_allin_v7_report.py
PYTHONPATH=. .venv/bin/python scripts/md_to_html.py \
  analysis/p0_pine_allin_v7_preholdout_20260820.md --out-dir analysis/html
```

The runner reads only the development and pre-holdout validation contract. Its
exclusive analysis end is 2026-03-01 UTC, 64 days before the repository's
2026-05-04 holdout boundary.

The notebook builder uses `nbformat`, `nbclient`, and `ipykernel` from the
throw-away `/tmp/fable-pine-eval-venv` evaluation environment. Those packages
must not be installed into the repository's version-contracted main venv.
