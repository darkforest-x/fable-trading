# Next Iteration Packet

model: deep

## Todo 9: Daily workflow smoke + pipeline health anomaly indicators

Todo 6 / 6B are done on VPS (redacted 7-stage API, fail-closed token auth,
executor off, desktop+390px screenshots). Plan Todo 7 (E2.1b report) stays
blocked/observe-only until training exits — do **not** start/stop E2.1b.
Start Todo 9 immediately; do not wait for owner Label Studio annotations.

1. Reconcile `codex/grok-2day`; reuse `.omo/evidence/task-6-vps-pipeline.md`.
2. Safe local sequence only: data status → forward main/shadows (idempotent) →
   digest dry-run → pipeline API. No Telegram send, no VPS job execution, no
   new market-data source swap.
3. Add **read-only** anomaly flags on pipeline payload for:
   - stale market data (15m mtime threshold)
   - judgment ACTIVE fingerprint mismatch
   - low forward sample vs decision target
   - YOLO diagnostic evidence missing/stale
   - executor unexpectedly on
4. Redaction unchanged: no secrets, absolute paths, holdout scores, write actions.
5. Tests: healthy + injected stale/mismatch; auth fail-closed still holds.
6. Optional light UI badges on 流水线. Redeploy VPS if code lands; prove
   executor=0 and anon 401.
7. Evidence `.omo/evidence/task-9-e2e-workflow.md`; update status + NEXT_TASK;
   commit/push non-secret tracked changes.

Pass: second forward run creates no duplicate rows; anomaly flags deterministic
from existing metadata; CLI/API agree; VPS executor remains off.

Boundaries: no holdout, live orders, model/label promotion, dataset overwrite,
Telegram token, force-push, main, or E2.1b start/stop.

Owner LS review remains parallel only (80 tasks / 53 prelabels / 0 annotations).
