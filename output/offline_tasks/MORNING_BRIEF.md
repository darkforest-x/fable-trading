# Morning brief (agent-maintained) — 2026-07-10 ~06:15 CST

Owner away (work day + weekend). Agent continues under `AUTONOMOUS_CHARTER.md`.

## One-liner
Mainline **frozen TP5/SL2 SWAP** + dual forward books healthy; **YOLO E2.1 still training** (best so far **ep13 mAP50=0.820**); expand **FINAL**; P2.5 **Phase0–3 on main**; FO/LS up.

## Critical path (do not break)
1. Do **not** eval holdout / swap ACTIVE without forward终审
2. Do **not** set VPS `ENABLE_JOB_EXECUTOR=1`
3. Do **not** kill `fable_yolo_e21_train` / finalize screens unless dead
4. BLOCKED auto-expand forbidden — candidates note only

## Live processes (screen)
| screen | purpose |
|--------|---------|
| `fable_yolo_e21_train` | E2.1 yolo11s retrain patience=12 |
| `fable_yolo_e21_finalize` | post-train formal report + consistency |
| `fable_resume_anime_mana` | finish ANIME/MANA `.part` |
| `fable_fiftyone` | :5151 |
| `fable_label_studio` (docker) | :8081 |
| `fable_audit_server_8643` | label audit HTML |
| `fable_overnight_status` | heartbeat md |

## Metrics snapshot
- forward_log.csv ≈ 10 lines (incl header)
- forward_log_h1_scaled.csv ≈ 9 lines
- E2.1 results.csv: 17 epochs logged; peak mAP50 **0.8203**
- pytest: **113 passed** (2026-07-10 cycle)
- disk: reclaimed pip/docker/go-build; free ~17GB (was 97%)

## When train finishes
1. Wait for finalize → `analysis/p2a_e21_train_report.md`
2. Gate mAP50≥0.90: expect **FAIL** if best stays ~0.82 → document, FO hardlist recompute on new preds
3. Commit + push report; no conf/IoU loosen

## fable 拍板
SWAP · EMA 8-55 · TP5/SL2 freeze · YOLO non-critical · H1 shadow only
