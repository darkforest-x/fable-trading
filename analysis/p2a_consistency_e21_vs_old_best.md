# Consistency: E2.1 GT vs old yolo11s best.pt preds

**Date**: 2026-07-10  
**Discipline**: no holdout; old weights vs new labels (expected drop).

## Command

```bash
.venv/bin/python -m src.detection.consistency_check \
  --dataset datasets/dense_15m_full --split val \
  --preds datasets/dense_15m_full/preds_val_conf30 \
  --out analysis/output/consistency_e21_labels_vs_old_best.json
```

## Result

| Metric | Value |
|--------|------:|
| n_images | 1255 |
| n_gt (E2.1) | 1297 |
| n_pred (old best) | 1495 |
| matched IoU≥0.5 | 643 |
| **match_rate vs GT** | **0.4958** |
| precision-like | 0.4301 |
| gate ≥0.95 | **false** |

## Reading

Old `dense_15m_full_s` weights were trained on **pad12 / unlimited-length** boxes.
E2.1 GT cores are shorter (MAX_DENSE=12). ~50% match rate is expected misalignment,
**not** a claim that E2.1 train failed. Re-run this script after `dense_15m_full_s_e21`
training finishes against new best.pt.

## Gate

Formal consistency ≥95% only meaningful **after** retrain on current labels.
