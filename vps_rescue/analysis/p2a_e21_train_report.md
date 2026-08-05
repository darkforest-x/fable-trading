# P2a YOLO E2.1 formal retrain report

**Date**: 2026-07-10T04:30:38.533966+00:00
**Labels**: MAX_DENSE_BARS=12, X_PAD_PX=6
**Model**: yolo11s, imgsz=960, batch=8, patience=12, SAFE_AUG
**Weights**: `runs/detect/runs/detect/dense_15m_full_s_e21/weights/best.pt`

## Official val (best.pt)
| metric | value |
|---|---:|
| mAP50 | 0.8503 |
| mAP50_95 | 0.6655 |
| precision | 0.8106 |
| recall | 0.7047 |

Gate mAP50≥0.90: **FAIL**

## Best from results.csv
{'epoch': 30, 'P': 0.82686, 'R': 0.69958, 'mAP50': 0.85509, 'mAP50_95': 0.65647}

## Consistency vs E2.1 GT
match_rate=0.5042 gate95=False

## Honesty
- Not 1:1 comparable to pre-E2 mAP 0.8569 (different GT).
- No holdout. Detection non-critical path.
