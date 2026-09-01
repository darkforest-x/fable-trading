# 15m L2 side-split regression v1

This experiment changes one variable only: the source experiment's single
mixed LONG/SHORT regressor becomes one LONG regressor and one SHORT regressor.
The source rows, causal 28-feature vectors, TP5/SL2/72 outcomes, 0.2% round-trip
cost, dependency representatives, chronological splits, and tune-q90 threshold
policy remain byte-pinned.

It does not train L1.5. L1.5 is a separate visual morphology task whose labels
must describe owner-approved global shape quality without future returns.

```bash
PYTHONPATH=. .venv/bin/python scripts/retrain_15m_ma_launch_l2_by_side.py --train-evaluate
PYTHONPATH=. .venv/bin/python scripts/retrain_15m_ma_launch_l2_by_side.py --verify
PYTHONPATH=. .venv/bin/python scripts/build_15m_ma_launch_l2_side_split_report.py
python3 scripts/md_to_html.py analysis/p3_15m_ma_launch_l2_side_split_20260901.md --out-dir analysis/html
```

No holdout, promotion, deployment, ACTIVE/frozen mutation, forward write,
Telegram send, or order is permitted.
