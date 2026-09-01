# 15m MA Launch L2 Global Context v1

This active experiment tests one question: can a causal LightGBM judgment
layer remove frozen Grade-A YOLO proposals that look locally plausible but are
poor in their 168-bar global context?

The immutable contract is in `preregistration.json`.  This experiment is
research-only.  It does not read the holdout, promote or deploy a model, mutate
forward state, send Telegram messages, or place orders.

Run phases are intentionally separated so the scanner and preregistration are
committed before any outcomes are generated:

```bash
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --freeze-snapshot
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --scan --device 0 --batch 32
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --build-dataset
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --train-evaluate
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --render
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --verify
```
