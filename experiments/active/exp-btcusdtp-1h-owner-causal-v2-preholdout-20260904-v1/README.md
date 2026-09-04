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

## Reproduce

```bash
PYTHONPATH=. .venv/bin/python scripts/backtest_btcusdtp_1h_owner_causal_v2_preholdout.py --stage development
git add experiments/active/exp-btcusdtp-1h-owner-causal-v2-preholdout-20260904-v1/results/selection_receipt.json
git commit -m "research: freeze BTC K1 K2 v2 selection"
PYTHONPATH=. .venv/bin/python scripts/backtest_btcusdtp_1h_owner_causal_v2_preholdout.py --stage validation
python3 scripts/md_to_html.py analysis/p1_btcusdtp_1h_owner_causal_v2_preholdout_20260904.md --out-dir analysis/html
```
