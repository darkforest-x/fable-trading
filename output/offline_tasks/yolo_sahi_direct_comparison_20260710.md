# YOLO Direct vs SAHI Sample Comparison

Generated: 2026-07-10 00:20 CST

Scope:

- Dataset: `datasets/dense_15m_full`
- Weights: `runs/detect/runs/detect/dense_15m_full_s/weights/best.pt`
- Sample: 80 deterministic validation images, seed `20260709`
- Confidence: `0.30`
- Match rule: one-to-one IoU >= 0.50 against validation labels
- This is a diagnostic sample comparison, not official mAP validation.

## Results

| Mode | GT boxes | Pred boxes | Matched IoU50 | Recall-like IoU50 | Pred / GT |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct YOLO | 97 | 106 | 77 | 0.7938 | 1.0928 |
| SAHI sliced | 97 | 178 | 75 | 0.7732 | 1.8351 |

## Reading

SAHI did not improve this YOLO setup on the sampled validation set. It produced fewer IoU50 matches and many more predicted boxes. The likely practical effect is more false positives, not a clean mAP lift.

Recommended action:

- Do not promote SAHI into the main detection path yet.
- Keep SAHI as a diagnostic experiment only.
- Prioritize label quality cleanup / boundary consistency before another SAHI round.
- Any later SAHI retry should be a single-variable experiment with tuned slice/NMS settings and a direct baseline in the same report.

Guardrails:

- No holdout was used.
- No training was run.
- No threshold preset, label rule, or cost assumption was changed.
