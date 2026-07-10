# Next Iteration Packet

model: deep

## Preferred next atom (non-owner-gated)

### Option A — Digest ↔ pipeline anomaly glue (small)

Wire `scripts/daily_digest.py` dry-run summary to include top pipeline anomaly
ids (read-only import of `collect_anomalies` / payload). Keep Telegram send
disabled by default. Tests for “healthy” vs injected flags. Evidence
`.omo/evidence/task-9b-digest-anomalies.md`.

### Option B — Todo 7 E2.1b report (only if training already exited)

**Observe-only:** if E2.1b finished, parse existing artifacts into
`analysis/p2a_e21b_hsv0_report.md`. Never start/stop training. If still running,
record observe snapshot and skip.

### Option C — Shadow forward idempotency slice

Run `forward_track_shadows` / H1 shadow once ×2; prove no duplicate rows; do not
change ACTIVE.

## Still parallel / owner gates

- Label Studio: 80 tasks, 53 prelabels, 0 human annotations.
- No holdout, live orders, model promote, force-push, main, Telegram paste.

## Already green (do not redo unless regression)

- VPS pipeline: 7 stages, anomalies, auth 401/200, executor 0, screenshots
  under `.omo/evidence/task-6-vps-screens/`.
- Commits: `dc441fa` and Todo9 follow-up on `codex/grok-2day`.
