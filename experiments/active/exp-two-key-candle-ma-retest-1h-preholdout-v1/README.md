# Two-key-candle MA retest — 1h pre-holdout research

This experiment tests the owner-described sequence as a causal trading
hypothesis rather than as a chart anecdote:

1. K1 displaces through a moving-average reference;
2. K2 occurs later, retests that reference with the side-specific wick, and
   closes back on the intended side;
3. entry is the next bar open and the initial stop is exactly the K2 extreme.

The feature surface includes the six-line SMA/EMA 20/60/120 rope, the
ChartPrime-style SMA40(HL2) candle colour, the MA Shift oscillator state,
confirmed 10/10 pivot-break state, candle geometry, volume, volatility,
four distinct K1-to-K2 distance families, and the intervening path.

The selection universe excludes BTC. Parameters are selected only on
2023–2024 half-year folds. The frozen rule is then evaluated on 2025 through
2026-02-28 and transferred to BTC without retuning. The repository holdout
starts on 2026-05-04 and is never read.

This is research-only. It does not train a model, alter the production
detector, promote a bundle, change execution settings, or place orders.

## Reproduce

```bash
PYTHONPATH=. .venv/bin/python scripts/research_two_key_candle_ma_retest_1h.py
PYTHONPATH=. .venv/bin/python scripts/validate_two_key_candle_ma_retest_1h.py
python3 scripts/md_to_html.py \
  analysis/p0_two_key_candle_ma_retest_1h_20260904.md \
  --out-dir analysis/html
```

