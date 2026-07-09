# Overnight autonomous run (2026-07-10)

Owner authorized: work all night without questions; use multi-subagent.

## Guardrails still enforced

- No holdout evaluation
- No free shell job types on VPS executor
- No secrets written to git
- No YOLO flip/mosaic/hsv direction-breaking aug
- Deploy only when milestone lands and script already approved

## Parallel tracks

| Track | Mechanism | Goal by morning |
|-------|-----------|-----------------|
| P2.5 Phase 2 job runner | worktree subagent | whitelist runner + 任务 tab + tests |
| SWAP expand FINAL | monitor subagent | report when fetch finishes |
| H1 shadow + forward docs | subagent | plans + forward smoke |
| YOLO E2.1 retrain | screen `fable_yolo_e21_train` | yolo11s on relabeled dense_15m_full |
| Val preds for FO | screen `fable_yolo_preds_val` | preds_val_conf30 for mistakenness |
| Status heartbeat | screen `fable_overnight_status` | OVERNIGHT_STATUS.md every 30m |

## Morning checklist (for next session)

1. `cat output/offline_tasks/OVERNIGHT_STATUS.md`
2. YOLO metrics in train log / `analysis/` if written
3. `git log --oneline -15` for Phase2 merge
4. Deploy VPS if new dashboard features merged
5. Open E2.1 compare + experiments tab
