# H-TIP — tip-firing for live YOLO

## Problem (2026-07-19 live)

Forward log: **0/10** rows with detection lag ≤55m. Mid-image labels teach
"cluster + launch already printed to the right"; live tip has no right context.

## Experiment (single variable)

| Item | Choice |
|------|--------|
| Base | `models/owner_best.pt` (v11) |
| Data | `dense_owner_v12_htip` = v11 ∪ tip clones (train only) |
| Build | `scripts/build_htip_dataset.py` |
| Train | chain finetune 40ep patience 10 AdamW 1e-4 |
| Metric | `tip_detectability.py --true-tip` + frozen owner F1 |
| Success | tip_hit_rate ≫ v11; frozen F1 not collapsed |

## Commands

```bash
PYTHONPATH=. .venv/bin/python scripts/build_htip_dataset.py
PYTHONPATH=. .venv/bin/python scripts/tip_detectability.py \
  --dataset datasets/dense_owner_v11 --split val --true-tip --limit 100 \
  --weights models/owner_best.pt --out analysis/output/tip_rate_v11.json
PYTHONPATH=. .venv/bin/python -m src.detection.train \
  --data datasets/dense_owner_v12_htip/data.yaml \
  --model models/owner_best.pt --epochs 40 --patience 10 --name owner_v12_htip
PYTHONPATH=. .venv/bin/python scripts/tip_detectability.py \
  --dataset datasets/dense_owner_v11 --split val --true-tip --limit 100 \
  --weights runs/detect/runs/detect/owner_v12_htip/weights/best.pt \
  --out analysis/output/tip_rate_v12.json
```

No holdout. Promote only after owner OK.
