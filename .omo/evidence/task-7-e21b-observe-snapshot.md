# Todo 7 — E2.1b observe snapshot (training NOT exited)

**Result: SKIP formal report (still running)**

**When:** 2026-07-10 17:19 local

**Observe-only:** no start/stop of training; no report file written

## Hypothesis gate

Formal `analysis/p2a_e21b_hsv0_report.md` only after training exits.
If still running → record snapshot and skip.

## Snapshot

| Field | Value |
|-------|--------|
| PID | 37441 |
| Elapsed | ~4h49m |
| cwd / name | fable-trading-codex · `dense_15m_full_s_e21b_hsv0` |
| args | epochs 40, imgsz 960, batch 8, patience 12, yolo11s.pt |
| log | `fable-trading-codex/output/offline_tasks/yolo_e21b_hsv0_20260710.log` |
| results.csv epochs | 12 completed rows (epoch 13 in progress) |
| best mAP50(B) so far | **0.51028** / mAP50-95 **0.28746** (epoch 7 val) |
| anomaly | epochs 8–11 completed with P/R/mAP equal to 0; epoch 12 recovered only to mAP50 0.31475 |
| finished marker | absent (`E2.1b train finished` not in log) |

## Actions taken

- None on the train process (observe-only).
- No `analysis/p2a_e21b_hsv0_report.md`.

## Next

Re-check for exit code + full results.csv; treat the four zero-validation rows
and weak epoch-12 recovery as a real instability signal, then write the formal
Todo 7 report.
