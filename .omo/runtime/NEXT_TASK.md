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

### Completed — q80 24-hour diagnostic

The first immutable ready snapshot covers exactly 24.0 market hours. q80 increased actionable
signals from 43 to 73, but fixed-cost economics failed for q90-range (25 closed, PF `0.4085`,
net/trade `-0.3613%`) and q80-only (15 closed, PF `0.5510`, net/trade `-0.2856%`). q80 promotion
is rejected; no threshold or ACTIVE change is authorized. The completed q80 loop was stopped.
Evidence: `analysis/ma206_q80_shadow_24h_report.md`,
`analysis/output/ma206_q80_shadow_24h.json`, and `.omo/evidence/task-q80-24h-final.md`.

### Active next atom — frozen forward confirmation

Continue the existing daily q90 and H1 paper books without changing the frozen model, threshold,
candidate rule, cost or exits. The next confirmation gate is at least 100 closed rows on a uniform
candidate-semantics window. Keep the two preserved legacy-semantics rows out of the uniform-window
economics, and do not use the q80 diagnostic window to tune parameters.

Latest accepted refresh (`2026-07-11 22:36 +08:00`): main 57 total / 39 closed and H1 57 / 40,
both with 0 duplicate. Excluding the two legacy rows leaves main 55 / 37 closed and H1 55 / 38.
At fixed 0.20% cost, uniform main PF is `0.9980` and H1 PF is `0.6056`; neither is accepted as
profitable. Evidence: `.omo/evidence/task-forward-refresh-20260711.md`.

Latest accepted refresh (`2026-07-12 00:38 +08:00`): main 59 total / 52 closed and H1 59 / 58,
both with 0 duplicate. Uniform semantics leaves main 57 / 50 closed and H1 57 / 56. At fixed
0.20% cost, main PF is `1.4356` and H1 PF is `1.2077`; both are positive but remain below the
100-closed gate. Evidence: `.omo/evidence/task-forward-refresh-20260712.md`.

Latest accepted refresh (`2026-07-12 04:30 +08:00`): main 67 total / 56 closed and H1 67 / 59,
both with 0 duplicate. Uniform semantics leaves main 65 / 54 closed and H1 65 / 57. At fixed
0.20% cost, main PF is `1.4330` and H1 PF is `1.3032`; both remain positive but below the gate.
Evidence: `.omo/evidence/task-forward-refresh-20260712-0430.md`.

Owner additionally requested 2m and 3m research on 2026-07-12. Treat each timeframe as a separate
shadow experiment: add data support first, never apply the 15m freeze directly, use chronological
pre-holdout evaluation and fixed cost/slippage reporting, and do not change ACTIVE or the 15m books.

Data support and BTC/ETH 120-day smoke history are complete for both bars. Next, expand a liquid
SWAP research pool and close the 2m pre-holdout experiment at the single pre-registered 18-hour
horizon (`h540`). Begin the independent 3m `h360` experiment only after the 2m report is closed.
Evidence: `analysis/p2b_hf_2m_3m_data_feasibility.md`.

## Still parallel / owner gates

- Label Studio: 80 tasks, 53 prelabels, 0 human annotations → writeback blocked.
- No holdout, live orders, model promote, force-push, main, Telegram paste.
- E2.1b and SAHI are closed failures; do not repeat either fixed recipe.

## Reconciled evidence

- Digest anomaly glue: `1856936`, dry-run anomaly_ids match pipeline.
- H1 shadow ×2: new_signals=0, dup_keys=0, ACTIVE + mainline SHA stable.
- Full shadow registry ×2: champion + H1 idempotent; H8/H10 unsupported and not approximated.
- Fingerprint mismatch diagnosed: the mutable dataset path was rewritten after freeze; metadata was not falsified to hide it.
- q80 24-hour immutable snapshot: SHA-256 `2aaa403c02ef73654c8cc709d01f8e7ce18589875d48a7bc037ca61121afc51c`; holdout false, mainline unchanged, 0 duplicate.
- Todo 6 is accepted on the current MA206 deployment by
  `.omo/evidence/task-6-vps-current-ma206-acceptance.md` and commit `3033c99`.
- Todo 9 is accepted by `analysis/p25_daily_workflow_acceptance_20260710.md` and commit `3c51c1c`.
- F1/F2/F4 pre-final checks are recorded in `analysis/two_day_pre_final_audit_20260710.md`;
  F3 and Todo 10 are closed by `analysis/two_day_final_audit_20260711.md`.
