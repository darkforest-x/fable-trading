# Next Iteration Packet

model: deep

## Current judgment mainline

- SMA20/60/120 + EMA20/60/120 only; do not restore or reuse 8-55 paths.
- ACTIVE: `models/frozen_tp5_sl2_swap_ma206_20260710.txt`.
- Forward books: `data/forward_log_ma206.csv` and `data/forward_log_h1_scaled_ma206.csv`, start 2026-07-10 10:30 UTC.
- Full-pool MA206 val maker PF is 1.072 (H9 EMA120: 1.154); no profitability claim and no threshold tuning from forward data.

## Preferred next atom (non-owner-gated)

### Completed — Todo 6 VPS pipeline acceptance

Closed on current MA206 deployment: public redacted `/api/pipeline`, authenticated controls,
all seven stages, forward caveat, YOLO diagnostic gate, executor off, no local paths/secrets,
and real desktop plus 390px browser QA. Evidence:
`.omo/evidence/task-6-vps-current-ma206-acceptance.md`.

### Completed — Todo 7 E2.1b formal report

E2.1b ended naturally. Official mAP50 `0.8505` and consistency `51.27%` both failed;
`analysis/p2a_e21b_hsv0_report.md` is final and the model was not promoted.

Evidence: `.omo/evidence/task-e21b-hsv0-final.md` and the tracked report.

### Completed — Todo 8 fixed SAHI benchmark

Fixed `640x371`, overlap `0.2` full-val benchmark completed. SAHI reduced matches
`665→625`, increased predictions `1629→2753`, and cost `11.27×` latency. It is rejected;
see `analysis/p2a_e21b_sahi_report.md`.

Evidence: tracked report plus `analysis/output/e21b_sahi_fixed_benchmark.json`.

### Completed — Todo 10 and final verification

Final evidence, 210 tests, VPS current E2.1b status, executor-off gate and durable reports are
reconciled in `analysis/two_day_final_audit_20260711.md`.

### Completed — judgment booster shadow benchmark

LightGBM, CatBoost, XGBoost and fixed equal-weight ensemble were compared on the identical
28-feature, pre-holdout chronological split. LightGBM remains ACTIVE. XGBoost's reused-val
top-decile net was only `+0.002%` at fixed 0.20% cost, so it is eligible only for a future
independent forward challenger; no model, threshold or ledger was promoted. CatBoost and the
highly-correlated ensemble were rejected for now. Evidence:
`analysis/shadow_booster_framework_comparison.md` and commit `858bc8f`.

### Active next atom — q80 24-hour diagnostic

Keep q80 shadow accumulating to at least 24 hours and continue the frozen forward books.
Do not tune from the short shadow sample. At 24 hours, snapshot the same-window funnel and
closed q90-range versus q80-only diagnostics without changing thresholds. The cycle now writes
`output/offline_tasks/q80_shadow_checkpoint_status.json` every run and atomically creates
`output/offline_tasks/q80_shadow_24h_ready.json` only on the first ready cycle. If the ready file
is absent, do not manufacture a report or rerun the scanner in parallel.

Latest integrity checkpoint at 2026-07-11 12:15 CST: `17.25/24` market hours, 167 candidates,
51 q80 ledger rows (20 closed, 31 open), 0 duplicate. Fixed-cost closed diagnostics remain
negative for both q90-range (PF `0.878`) and q80-only (PF `0.635`); this is still too short for
threshold selection.

## Still parallel / owner gates

- Label Studio: 80 tasks, 53 prelabels, 0 human annotations → writeback blocked.
- No holdout, live orders, model promote, force-push, main, Telegram paste.
- E2.1b and SAHI are closed failures; do not repeat either fixed recipe.

## Reconciled evidence

- Digest anomaly glue: `1856936`, dry-run anomaly_ids match pipeline.
- H1 shadow ×2: new_signals=0, dup_keys=0, ACTIVE + mainline SHA stable.
- Full shadow registry ×2: champion + H1 idempotent; H8/H10 unsupported and not approximated.
- Fingerprint mismatch diagnosed: the mutable dataset path was rewritten after freeze; metadata was not falsified to hide it.
- Todo 6 is accepted on the current MA206 deployment by
  `.omo/evidence/task-6-vps-current-ma206-acceptance.md` and commit `3033c99`.
- Todo 9 is accepted by `analysis/p25_daily_workflow_acceptance_20260710.md` and commit `3c51c1c`.
- F1/F2/F4 pre-final checks are recorded in `analysis/two_day_pre_final_audit_20260710.md`;
  F3 and Todo 10 are closed by `analysis/two_day_final_audit_20260711.md`.
