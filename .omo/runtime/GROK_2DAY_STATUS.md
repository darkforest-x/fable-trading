# Codex Two-Day Status

- state: RUNNING_CODEX_ONLY
- branch: codex/grok-2day
- executor: codex
- grok_worker: stopped_by_owner_request_2026-07-10T16:20+08:00
- scheduler: stopped
- codex_heartbeat: e2-1b active every 4 hours; Codex-only execution
- current_todo: await-e21b-exit-then-formal-report
- last_slot: 2026-07-10T16:18 three-iter-digest-h1-e21bobserve
- last_result: E2.1b still running at epoch 13; best mAP50 0.51028 (epoch 7); epochs 8-11 zero, epoch 12 recovered to 0.31475
- next_action: Codex observes E2.1b natural exit, then writes Todo7 formal report
- final_complete: false

## Guardrails

- No judgment holdout or consumed trading validation reads.
- No owner-gated parameter, frozen candidate, secret, or live-order changes.
- VPS Label Studio deploy is authorized for Todo 4A only; no force-push; no main.
- No duplicate or stopped training; E2.1b is observe-only.
- Push only codex/grok-2day; never merge or push main.
- Compromised Telegram paste never used.
- VPS `ENABLE_JOB_EXECUTOR=0`; ops token only in root `/etc/fable-trading/ops.env`.

## Completed (this batch)

1. Todo 9b digest ↔ pipeline anomalies — `1856936` + `.omo/evidence/task-9b-digest-anomalies.md`
2. H1 shadow forward ×2 idempotency — `.omo/evidence/task-9c-h1-shadow-idempotency.md` (data/ untracked)
3. E2.1b observe snapshot (not exited) — `.omo/evidence/task-7-e21b-observe-snapshot.md`
4. Full multi-book shadow matrix ×2 idempotent — `.omo/evidence/task-shadow-matrix-idempotency.md`
5. ACTIVE fingerprint mismatch root cause confirmed — `.omo/evidence/task-fingerprint-mismatch-diagnostic.md`

## Prior green (do not redo)

- VPS pipeline 7 stages + anomalies + auth; Todo 6B/9; mainline forward idempotent

## Blocked Or Deferred

- Telegram until token rotation + chat ID.
- Full-80 annotation writeback waits owner LS (0 annotations).
- Todo 7 formal report waits E2.1b exit.
