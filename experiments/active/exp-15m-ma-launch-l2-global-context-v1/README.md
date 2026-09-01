# 15m MA Launch L2 Global Context v1

This active experiment tests one question: can a causal LightGBM judgment
layer remove frozen Grade-A YOLO proposals that look locally plausible but are
poor in their 168-bar global context?

Before any L2 outcomes or scores were generated, the split audit found that an
18-hour label-only purge did not isolate the 168-bar input history.  The frozen
integrity amendment in `preregistration.json` therefore uses a 60-hour
input-plus-label embargo and fits/evaluates only the earliest event in each
connected full-exposure dependency block.  Later overlapping events remain
available for scoring and visual review, but never count as independent rows.

The upstream five-model comparison is frozen as lineage, not pooled training
data.  Its checkpoints have incompatible label/window/native-resolution and
confidence contracts.  V1 therefore attaches L2 to the current Owner Grade-A
full40 native-1280 arm only; that choice is based on the current data/geometry
contract, not recent signal counts or holdout outcomes.

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
PYTHONPATH=. .venv/bin/python scripts/build_15m_ma_launch_l2_global_context_report.py
```
