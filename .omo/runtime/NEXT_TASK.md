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

### Option A — Todo 7 E2.1b formal report (only if training exited)

**Observe-only:** if `dense_15m_full_s_e21b_hsv0` finished (log has
`E2.1b train finished` / process gone), parse
`runs/.../dense_15m_full_s_e21b_hsv0/results.csv` + log into
`analysis/p2a_e21b_hsv0_report.md`. Never start/stop training.
If still running, refresh the observe snapshot only when facts change and execute independent
pre-final checks. Do not start Option B before Option A.

Evidence path: `.omo/evidence/task-7-e21b-report.md` when done.

### Option B — Todo 8 fixed SAHI benchmark (only after Option A)

Use the approved isolated SAHI environment. Predeclare and keep fixed:
`slice_width=640`, `slice_height=371`, overlap `0.2`, and the existing
confidence/IoU definitions. Reconcile a tiny sample before full val. Report
latency and clearly named metrics in `analysis/p2a_e21b_sahi_report.md`; never
call a custom evaluator an official Ultralytics metric unless definitions are
identical. Do not modify the main `.venv`.

Evidence path: `.omo/evidence/task-8-sahi.txt`.

### Option C — Todo 10 and final verification (after Options A/B)

Reconcile completed evidence, focused tests, VPS executor-off state, tracked
diff and secrets scan. Update the durable project status reports with a clear
separation between historical backtest, short forward observation, and
unproven future returns. Do not merge or push main.

## Still parallel / owner gates

- Label Studio: 80 tasks, 53 prelabels, 0 human annotations → writeback blocked.
- No holdout, live orders, model promote, force-push, main, Telegram paste.
- E2.1b train remains observe-only until natural exit.

## Reconciled evidence (does not close Todo 7/8/final)

- Digest anomaly glue: `1856936`, dry-run anomaly_ids match pipeline.
- H1 shadow ×2: new_signals=0, dup_keys=0, ACTIVE + mainline SHA stable.
- Full shadow registry ×2: champion + H1 idempotent; H8/H10 unsupported and not approximated.
- Fingerprint mismatch diagnosed: the mutable dataset path was rewritten after freeze; metadata was not falsified to hide it.
- Todo 6 is accepted on the current MA206 deployment by
  `.omo/evidence/task-6-vps-current-ma206-acceptance.md` and commit `3033c99`.
- Todo 9 is accepted by `analysis/p25_daily_workflow_acceptance_20260710.md` and commit `3c51c1c`.
- F1/F2/F4 pre-final checks are recorded in `analysis/two_day_pre_final_audit_20260710.md`;
  F3 still waits for Todo 7/8.
