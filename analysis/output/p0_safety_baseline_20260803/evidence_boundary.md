# P0-SAFETY baseline evidence boundary — 2026-08-03

- Audited the local full repository at `4333fa722b6a98fdaa8a36f37f1d468d43956b5f` on `main`; the worktree was clean and matched `origin/main`.
- Read repository code, metadata sidecars, and the Notion 00/02/03/04/05/07 specifications.
- For `data/judgment_v10_wide.csv`, `data/judgment_yolo_swap_v10.csv`, `data/forward_log.csv`, and `data/executor_ledger.jsonl`, collected only existence, byte size, modification time, and whole-file SHA256. No rows were parsed, scored, charted, or summarized.
- No source with `signal_time >= 2026-05-04` was inspected or evaluated. No holdout metric was produced.
- No VPS connection was made. Repository timer/deploy files prove only intended configuration, not current service state.
- The audit has no direct evidence of live systemd unit contents, running processes, remote log output, remote forward/ledger contents, kill-switch state, demo/live mode, account state, positions, or orders.
- `models/active_bundle.json` was absent. The existing example bundle is not an activation artifact.
- Runtime identity was not unified at baseline: forward used `models/ACTIVE` with a latest-artifact fallback, dashboard read `models/ACTIVE`, and executor trusted the forward CSV without bundle validation.
- No training, threshold change, ACTIVE change, detector promotion, forward-log mutation, deployment, service restart, API-key access, or trading-client call occurred.
