# Codex Two-Day Status

- state: RUNNING_CODEX_ONLY
- branch: codex/grok-2day
- executor: codex
- grok_worker: stopped_by_owner_request_2026-07-10T16:20+08:00
- scheduler: stopped
- codex_heartbeat: active every 4 hours; Codex-only execution
- current_todo: frozen q90 and H1 forward monitoring toward 100 closed rows
- last_slot: 2026-07-12T00:38 frozen-forward-refresh
- last_result: q90 main 59 total / 52 closed and H1 59 / 58, both with 0 duplicate; uniform main is 50 closed / PF 1.4356 and H1 is 56 / PF 1.2077 at fixed 0.20% cost, still below the 100-closed gate
- next_action: continue the existing daily q90/H1 paper books unchanged until at least 100 uniform-semantics closed rows; report the fixed 0.20% economics at that pre-registered checkpoint
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
14. q80 24h auto-seal — first market-time-ready snapshot is atomic and never overwritten; 213 tests pass
15. Frozen LightGBM score explanations — `5d13413`; 21/21 scores replayed, 19 current-candidate rows and 2 preserved legacy-semantics rows
16. Booster shadow benchmark — `858bc8f`; LightGBM remains ACTIVE, XGBoost is forward-challenger-only, CatBoost and equal-weight ensemble rejected for now; 218 tests pass
17. q80 24-hour final — `analysis/ma206_q80_shadow_24h_report.md`; q80 increased actionable signals 69.8% but q90-range and q80-only both failed fixed-cost economics; q80 loop stopped after immutable snapshot
18. Frozen daily forward refresh — `.omo/evidence/task-forward-refresh-20260711.md`; 456-symbol data refresh, q90/H1 ledgers, dry-run digest and VPS deploy passed; orphan q80 updater was removed after process-group diagnosis
19. Frozen daily forward refresh — `.omo/evidence/task-forward-refresh-20260712.md`; 4,073 new bars, q90/H1 uniform economics positive at fixed cost but only 50/56 closed; digest and VPS passed
20. 2m/3m shadow data support — `analysis/p2b_hf_2m_3m_data_feasibility.md`; OKX live API and BTC/ETH 120-day files passed continuity checks; no model or mainline change

## Implemented but not accepted as profitable

- Engineering pipeline and final detector evidence are complete; future profitability is still unproven.
- MA206 q90 and H1 books need forward accumulation; no threshold may be selected from the short forward sample.
- XGBoost's reused-val top-decile net was approximately flat (`+0.002%` at 0.20% cost); it is not promotion evidence.
- q80 is closed as a failed promotion candidate; its 24-hour result must not be reused for threshold selection.

## Blocked Or Deferred

- Telegram until token rotation + chat ID.
- Full-80 annotation writeback waits owner LS (0 annotations).
