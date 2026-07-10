# Codex Two-Day Status

- state: RUNNING_CODEX_ONLY
- branch: codex/grok-2day
- executor: codex
- grok_worker: stopped_by_owner_request_2026-07-10T16:20+08:00
- scheduler: stopped
- codex_heartbeat: active every 4 hours; Codex-only execution
- current_todo: q80 24-hour diagnostic accumulation and frozen forward monitoring
- last_slot: 2026-07-11T04:20 fixed-sahi-and-direction-economics
- last_result: E2.1b, fixed SAHI, and causal direction YOLO all failed their gates; q80 shadow remains diagnostic-only
- next_action: keep q80 shadow accumulating to 24h; only forward evidence may change the profitability conclusion
- final_complete: true

## Guardrails

- MA206 holdout was accidentally scored once by the legacy dashboard during migration QA; quarantined and guarded by pre_holdout_only cache scope.
- Owner approved the MA206 architecture/freeze migration on 2026-07-10; no further parameter, secret, or live-order changes.
- VPS Label Studio deploy is authorized for Todo 4A only; no force-push; no main.
- No duplicate training; E2.1b ended naturally and no detector was promoted.
- Push only codex/grok-2day; never merge or push main.
- Compromised Telegram paste never used.
- VPS `ENABLE_JOB_EXECUTOR=0`; ops token only in root `/etc/fable-trading/ops.env`.

## Completed (this batch)

0. MA206 full-path migration — ACTIVE `frozen_tp5_sl2_swap_ma206_20260710`; new forward start 2026-07-10 10:30 UTC
1. Todo 9b digest ↔ pipeline anomalies — `1856936` + `.omo/evidence/task-9b-digest-anomalies.md`
2. H1 shadow forward ×2 idempotency — `.omo/evidence/task-9c-h1-shadow-idempotency.md` (data/ untracked)
3. E2.1b observe snapshot (not exited) — `.omo/evidence/task-7-e21b-observe-snapshot.md`
4. Full multi-book shadow matrix ×2 idempotent — `.omo/evidence/task-shadow-matrix-idempotency.md`
5. ACTIVE fingerprint mismatch root cause confirmed — `.omo/evidence/task-fingerprint-mismatch-diagnostic.md`
6. Todo 6 current-MA206 VPS acceptance — `.omo/evidence/task-6-vps-current-ma206-acceptance.md`; public redacted pipeline 200, ops auth retained, executor 0, desktop/mobile passed
7. Current-MA206 daily workflow — `.omo/evidence/task-10-daily-workflow-current-ma206.md`; forward byte-idempotent, digest dry-run, VPS data mirror 456/456, Codex daily automation active
8. F1/F2/F4 pre-final audit — `analysis/two_day_pre_final_audit_20260710.md`; 173 tests, secret/dependency/scope checks, VPS executor 0
9. MA206 profitability diagnosis — `analysis/ma206_profitability_diagnosis.md`; cost bridge, generalization drop, score/return alignment and rejected causes
10. E2.1b final report — `analysis/p2a_e21b_hsv0_report.md`; mAP50 0.8505, consistency 51.27%, rejected
11. Fixed SAHI full val — `analysis/p2a_e21b_sahi_report.md`; 625/1297 matched, 2753 predictions, rejected
12. Causal direction YOLO — `analysis/p2a_causal_direction_profit_report.md`; net@0.2% negative, PF 0.7472, rejected
13. q80 same-window shadow — `analysis/ma206_q80_shadow_diagnosis.md`; separate ledger, no ACTIVE/main-book writes

## Implemented but not accepted as profitable

- Engineering pipeline and final detector evidence are complete; future profitability is still unproven.
- MA206 q90 and q80 books need forward accumulation; no threshold may be selected from the short shadow sample.

## Blocked Or Deferred

- Telegram until token rotation + chat ID.
- Full-80 annotation writeback waits owner LS (0 annotations).
