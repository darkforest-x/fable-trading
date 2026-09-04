# BTCUSDT.P 1h owner-causal K1→K2 v2

This research-only experiment implements the owner-approved corrections to the
current Pine Core Recall rule:

1. K1 MA-side colour must agree with trade direction;
2. every bar strictly between K1 and K2 must both close and colour on the
   intended side of SMA40;
3. K2 must physically touch SMA40 with its rejection wick while the entire
   candle body remains on the intended side;
4. K1 body/range increases from 0.50 to 0.65;
5. a fee-to-risk maximum is selected on development data;
6. a causal fee-cover profit-protection stop is enabled only if its development
   evidence clears the preregistered improvement rule.

The exact K2 stop, 3R target, 12-hour horizon, conservative intrabar collision
rule, and 0.2% round-trip cost remain unchanged. The source file physically
ends on 2026-02-28, before the repository holdout begins on 2026-05-04.

Development is 2023–2024. After the selection receipt is committed, validation
is 2025 through 2026-02-28 with no retuning. No model is trained or promoted,
no ACTIVE/frozen/forward state is changed, and no order is placed.

## Outcome

Rejected for production economics. On frozen validation the baseline averaged
-19.26 bp per trade after cost; the fixed structural bundle improved this to
-8.73 bp, and the development-selected 1.25R fee-to-risk ceiling improved it to
-4.35 bp across 22 trades. Gross expectancy was +15.65 bp, below the frozen
20 bp round-trip cost. Matched-control excess was +11.36 bp with one-sided
sign-flip p=0.1994. The 1.5R protection rule armed on five trades and changed no
exit. See the canonical report for path failures and time-slice stability.

## Reproduce

```bash
PYTHONPATH=. .venv/bin/python scripts/backtest_btcusdtp_1h_owner_causal_v2_preholdout.py --stage development
git add experiments/active/exp-btcusdtp-1h-owner-causal-v2-preholdout-20260904-v1/results/selection_receipt.json
git commit -m "research: freeze BTC K1 K2 v2 selection"
PYTHONPATH=. .venv/bin/python scripts/backtest_btcusdtp_1h_owner_causal_v2_preholdout.py --stage validation
PYTHONPATH=. .venv/bin/python scripts/analyze_btcusdtp_1h_owner_causal_v2_results.py
PYTHONPATH=. .venv/bin/pytest -q tests/test_backtest_btcusdtp_1h_owner_causal_v2_preholdout.py
python3 scripts/md_to_html.py analysis/p1_btcusdtp_1h_owner_causal_v2_preholdout_20260904.md --out-dir analysis/html
```
