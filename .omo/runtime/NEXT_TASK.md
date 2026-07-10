# Next Iteration Packet

model: deep

## Preferred next atom (non-owner-gated)

### Option A — Todo 7 E2.1b formal report (only if training exited)

**Observe-only:** if `dense_15m_full_s_e21b_hsv0` finished (log has
`E2.1b train finished` / process gone), parse
`runs/.../dense_15m_full_s_e21b_hsv0/results.csv` + log into
`analysis/p2a_e21b_hsv0_report.md`. Never start/stop training.
If still running, refresh observe snapshot and pick Option B/C.

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

## Already green (do not redo unless regression)

- Digest anomaly glue: `1856936`, dry-run anomaly_ids match pipeline.
- H1 shadow ×2: new_signals=0, dup_keys=0, ACTIVE + mainline SHA stable.
- Full shadow registry ×2: champion + H1 idempotent; H8/H10 unsupported and not approximated.
- Fingerprint mismatch diagnosed: the mutable dataset path was rewritten after freeze; metadata was not falsified to hide it.
- VPS pipeline anomalies + auth + executor 0.
- Mainline forward idempotent (Todo 9).
