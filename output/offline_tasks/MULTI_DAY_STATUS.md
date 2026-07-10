# Multi-day status 2026-07-10T04:36:49.636702+00:00

Owner away. 2h durable tick.

## fable / red lines
SWAP · SMA/EMA 20/60/120 · TP5/SL2 freeze · YOLO non-critical · H1 shadow
no holdout · no secrets · VPS `ENABLE_JOB_EXECUTOR=0`

## Checklist
| item | status |
|------|--------|
| train done? | **YES** E2.1 formal DONE (mAP50=0.8503 **FAIL** gate 0.90) |
| expand done? | **YES** 401 · 0 parts · FINAL |
| FO/LS up? | FO 200 · LS 302 · docker Up |
| forward_track? | main 9 (2o/7c) **new=0**; H1 8 (1o/7c) **new=0** |
| merge branches? | Phase2/3 on main |
| pytest? | **114 passed** |
| deploy? | no UI change → skip; executor=False |
| next | **P0 forward life**; optional secondary e21b_hsv0 train running (non-mainline); docs sync |

## E2.1 formal (locked)
| metric | value |
|--------|------:|
| mAP50 | 0.8503 |
| mAP50-95 | 0.6655 |
| P / R | 0.8106 / 0.7047 |
| consistency match | 0.5042 |
| gate_match≥0.95 | False |

Reports: `analysis/p2a_e21_train_report.md`, hardlist `output/offline_tasks/fiftyone_hard_e21/`

## Side note
Secondary screen `fable_yolo_e21b_hsv0` started from codex worktree (same SAFE_AUG train CLI; name only). Not promoted; mainline unchanged.
ACTIVE=`models/frozen_tp5_sl2_swap_ma206_20260710.txt`
