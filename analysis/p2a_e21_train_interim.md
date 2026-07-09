# YOLO E2.1 training interim (in progress)

**Updated**: live while train runs

Run dir: `runs/detect/runs/detect/dense_15m_full_s_e21`

Labels: E2.1 (`MAX_DENSE_BARS=12`, `X_PAD_PX=6`). Model yolo11s, imgsz=960, batch=8, patience=12, SAFE_AUG.

| epoch | P | R | mAP50 |
|---:|---:|---:|---:|
| 1 | 0.3272 | 0.3874 | 0.2729 |
| 2 | 0.1091 | 0.0046 | 0.0011 |
| 3 | 0.2199 | 0.0324 | 0.0149 |
| 4 | 0.5339 | 0.5682 | 0.5565 |
| 5 | 0.6231 | 0.6361 | 0.6642 |
| 6 | 0.4624 | 0.4880 | 0.4967 |
| 7 | 1.0000 | 0.0008 | 0.0050 |
| 8 | 0.1519 | 0.4533 | 0.1128 |

**Best so far**: epoch 5 mAP50=0.6642 P=0.6231 R=0.6361

## Notes

- Epochs 2–3 and 7 show collapse spikes; best weights kept at peak (patience).
- Full report `analysis/p2a_e21_train_report.md` written only after train exit.
- Old formal best was 0.8569 on pre-E2 labels — not directly comparable.

