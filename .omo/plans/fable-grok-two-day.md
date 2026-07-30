# fable-grok-two-day - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** A two-day, low-token Grok worker that strengthens the trading evidence, tracks several fixed strategy challengers prospectively, finishes the compliant YOLO evaluation plus one fixed SAHI benchmark, and deploys a VPS read-only view of the whole pipeline.

**Why this approach:** Grok continues immediately after each successful atomic task, while quota, rate-limit, network, or task failures back off for five hours. Codex checks only every five hours. All edits stay on an isolated branch so active jobs and user files are preserved.

**What it will NOT do:** It will not promise future profit, inspect trading holdout data, tune on the consumed validation window, alter the frozen live candidate, enable VPS execution, place orders, commit secrets, or merge to main.

**Effort:** Large
**Risk:** Medium - concurrent runtime jobs and a dirty main worktree require strict branch and lock discipline.
**Decisions to sanity-check:** Continuous Grok success path with five-hour failure backoff; isolated branch only; historical positive results remain candidate evidence until enough new forward trades accumulate.

Your next move: Execution is pre-authorized for the two-day absence; review the final branch and owner-gated list on return. Full execution detail follows below.

---

> TL;DR (machine): 48h isolated Grok loop, 60s success continuation, 5h failure backoff, strategy/shadow/VPS/E2.1b/SAHI, no holdout/live mutation.

## Autonomous iteration protocol

This plan is a living queue, not a completion checklist. Every completed or
failed todo must produce a comparison against baseline, a bottleneck diagnosis,
and the next testable hypothesis. Reorder, split, replace, or append todos when
evidence changes the expected value. Do not wait for the owner between routine
iterations.

Each loop is mandatory: measure current state -> choose the largest bottleneck
-> predeclare hypothesis and pass/fail criteria -> implement one bounded change
-> run tests and the real surface -> compare with baseline -> record success or
failure -> commit/push -> enqueue the next hypothesis. A workstream stops only
when its gate passes, evidence shows further work has negative value, an explicit
safety boundary blocks it, or the forty-eight-hour deadline is reached.

Active gates:
- Strategy: cost-realistic, no-lookahead stability plus prospective paper data;
  positive historical numbers alone do not pass.
- YOLO: versioned data provenance, globally chronological validation, human gold
  audit quality, mAP/consistency/error categories, and deployment-domain fit.
- VPS: public read-only project flow plus authenticated manual labeling, with
  executor off and real browser verification.
- Reliability: reproducible commands, focused/full tests, runtime evidence,
  atomic commits, pushed branch, and no secret leakage.

## Scope
### Must have
- Durable file-backed queue and status that survive Grok session-registry failure.
- Atomic single-worker lock, immediate continuation after success, five-hour failure backoff, about forty-eight-hour deadline, and concise logs.
- Keep the worker synchronized with the latest verified `main` while preserving the HSV fix from commit `43c7469`.
- Forward/data/digest health evidence and a pre-holdout walk-forward stability report for already-frozen strategy candidates, with no further parameter search.
- Prospective shadow logs for the main candidate and predeclared challengers; no automatic ACTIVE promotion.
- P2.5 tests, local auth/API/browser QA, and a deployable VPS read-only pipeline view with executor off.
- E2.1b args/metrics/consistency/hard-case report and one fixed-parameter SAHI benchmark in the already-approved isolated environment.
- Atomic commits and pushes only to `codex/grok-2day`; Chinese status and final report.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- No judgment holdout or consumed trading-validation-window reread. Historical stability work must end strictly before `2026-05-04` and may not be used to claim final performance.
- No threshold/TP-SL/ATR/cost/frozen-ACTIVE/blacklist mutation. New strategy ideas may run shadow-only with predeclared settings and must never replace the frozen mainline automatically.
- No duplicate train/fetch/finalize process; do not stop healthy screens. Never run `scripts/_yolo_e21_finalize.sh` because it hardcodes main and pushes main.
- No real or demo key creation, live order, secret/token commit, VPS `ENABLE_JOB_EXECUTOR=1`, force push, destructive cleanup, dependency migration, or merge to main.
- No broad scans of `data/`, `datasets/`, `runs/`, generated HTML, or old logs unless a named todo requires an exact path. VPS output must not expose absolute paths, secrets, or write controls.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD for reproduced bug fixes; tests-after for audits/reports; pytest plus real CLI/API/browser checks.
- Every invocation writes concise evidence under `.omo/evidence/` and updates `.omo/runtime/GROK_2DAY_STATUS.md` without committing runtime files.
- Product changes are not complete until the exact focused test, full relevant suite, and matching real surface pass.

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- Wave 0, every wake: acquire `.omo/runtime/grok-worker.lock`, read only this plan plus compact status, check `screen -ls`, branch/upstream, and named runtime files; release lock on exit.
- Wave 1, while E2.1b trains: Todos 1-6. The single Grok worker executes one atomic todo per invocation while training and the local pulse run in parallel.
- Wave 2, after E2.1b exits: Todos 7-9.
- Wave 3, deadline or queue completion: Todo 10 and final verification wave.
- Cadence: `scripts/multi_day_pulse.sh` remains hourly without model tokens. Grok waits only 60 seconds after success and then takes the next todo. Non-zero exit, quota/rate-limit text, network failure, or approval request sleeps five hours. Codex heartbeat remains every five hours.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 2-10 | E2.1b training |
| 2 | 1 | 3, 4, 10 | 5, E2.1b training |
| 3 | 2 | 4, 10 | 5, E2.1b training |
| 4 | 2, 3 | 9, 10 | 5, E2.1b training |
| 5 | 1 | 6, 9, 10 | 2-4, E2.1b training |
| 6 | 5 | 9, 10 | 3, 4, E2.1b training |
| 7 | E2.1b exited, 1 | 8, 10 | 2-6 |
| 8 | 7 | 10 | 9 |
| 9 | 4, 6 | 10 | 7, 8 |
| 10 | 1-9 or 48h deadline | final wave | none |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. Port the HSV-zero compliance fix onto the current baseline
  What to do / Must NOT do: Inspect commit `46fadec`; port `hsv_s=0.0`, `hsv_v=0.0`, the focused regression test, learning note, and honest report correction onto this branch. Preserve newer E2/E2.1 text; do not cherry-pick blindly if docs conflict; do not start training.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 3, 6, 8
  References (executor has NO interview context - be exhaustive): `AGENTS.md:14-19`, `src/detection/train.py:19-31`, `analysis/p2a_detection_report.md:103-165`, commit `46fadec`.
  Acceptance criteria (agent-executable): `rg -n 'hsv_[hsv]=0.0' src/detection/train.py`; `PYTHONPATH=/Users/zhangzc/fable-trading/.venv/lib/python3.9/site-packages:. python3 -m pytest tests/test_detection_train_config.py -q`; all seven forbidden keys are zero; `git diff --check` passes.
  QA scenarios (name the exact tool + invocation): happy - inspect the already-running E2.1b launch line in `/Users/zhangzc/fable-trading-codex/output/offline_tasks/yolo_e21b_hsv0_20260710.log` and capture all zero args; failure - any non-zero key blocks completion and no new run starts. Evidence `.omo/evidence/task-1-hsv-compliance.txt`.
  Commit: Y | `Fix YOLO HSV augmentation compliance on current baseline`

- [x] 2. Establish the current end-to-end health baseline
  What to do / Must NOT do: Record active screens, SWAP 15m count, `.part.csv` count, main/H1 forward rows and states, latest market-data timestamp, frozen model fingerprint, and a non-sending digest. Run the existing forward command once only if no matching process is active, then prove idempotency. Never send Telegram, change a model, or kill a healthy job.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 3, 4, 10
  References: `output/offline_tasks/AUTONOMOUS_CHARTER.md`, `scripts/forward_track.py`, `scripts/daily_digest.py`, `/Users/zhangzc/fable-trading/data/forward_log.csv`, `/Users/zhangzc/fable-trading/data/forward_log_h1_scaled.csv`.
  Acceptance criteria: exact counts/timestamps and duplicate-key check are recorded; frozen model path/SHA and ACTIVE choice are unchanged; every incomplete `.part.csv` has a reason.
  QA scenarios: use named files and `ps`/`screen`, then run a non-sending digest. Any traceback becomes a focused bug with a regression test; no silent data-source substitution. Evidence `.omo/evidence/task-2-system-health.txt`.
  Commit: N unless a reproduced defect is fixed.

- [x] 3. Build a no-tuning pre-holdout strategy stability audit
  What to do / Must NOT do: Evaluate only predeclared existing candidates (frozen TP5/SL2 long, H1 scaled, H8 30m, H10 short when their existing artifacts are reproducible) on rolling time folds whose source rows all end before `2026-05-04`. Filter the source by time before feature/label construction. Do not read, score, or summarize judgment holdout; do not search parameters or choose a new threshold from results.
  Parallelization: Wave 1 | Blocked by: 2 | Blocks: 4, 10
  References: `analysis/p2b_v3_barrier_sweep.md`, `analysis/p15_h1_h2_exit_report.md`, `analysis/p2b_mtf_report.md`, `scripts/backtest.py`, `scripts/exit_variants_sweep.py`, `scripts/mtf_sweep.py`, `scripts/short_replication.py`.
  Acceptance criteria: a reproducible script/test and `analysis/strategy_stability_preholdout.md` report per-fold trades, PF, net/trade, win rate, max drawdown, maker fill, and real-funding coverage; the report labels all results historical candidate evidence, not final profitability proof.
  QA scenarios: happy - at least four chronological folds reconcile to raw trade counts; failure - a date assertion aborts if any input/output timestamp reaches `2026-05-04`. Evidence `.omo/evidence/task-3-strategy-stability.txt`.
  Commit: Y | `Add pre-holdout strategy stability audit`

- [x] 4. Add prospective champion-challenger shadow tracking
  What to do / Must NOT do: Extend the append-only forward infrastructure so the frozen TP5/SL2 champion and existing H1/30m/short challengers can be logged separately with fixed, predeclared configs. Keep ACTIVE and user-facing mainline unchanged. No candidate is promoted from two days of data, even if PnL is positive.
  Parallelization: Wave 1 | Blocked by: 2, 3 | Blocks: 9, 10
  References: `scripts/forward_track.py`, `scripts/forward_track_h1.py`, `src/judgment/forward.py`, `data/forward_log.csv` schema, reports named in Todo 3.
  Acceptance criteria: registry/config names are explicit; each log is append-only and idempotent on `(source,symbol,signal_time)`; tests cover no lookahead and duplicate prevention; dry run produces a compact comparison consumed by the digest without changing ACTIVE.
  QA scenarios: two consecutive runs add no duplicate rows; any unavailable challenger is marked unsupported rather than approximated. Evidence `.omo/evidence/task-4-shadow-forward.txt`.
  Commit: Y | `Track fixed strategy challengers prospectively`

- [x] 4A. Deploy a secure public Label Studio sampling workflow
  What to do / Must NOT do: Use Label Studio as the public manual box editor and keep FiftyOne local as the hard-case/model-vs-label triage tool. On the VPS, deploy a resource-bounded Label Studio service with built-in login, registration disabled, a generated strong credential stored only in root-owned VPS env plus an untracked local access note, and only the 80-image stratified/hard-case review pack. Never publish the full dataset, expose FiftyOne/MongoDB directly, enable the fable job executor, or reuse the Telegram token pasted in chat.
  Parallelization: Wave 1 | Blocked by: 4 or current task completion | Blocks: 6, 9, 10
  References: `scripts/start_label_studio_review.sh`, `scripts/label_studio_prepare_import.py`, `scripts/label_studio_compose.yml`, `output/label_studio/tasks_val.json`, `output/label_studio/label_config.xml`, `scripts/fiftyone_label_audit.py`, VPS `103.214.174.58`.
  Acceptance criteria: systemd service survives restart with memory cap; signup is disabled; anonymous access cannot view tasks; authenticated browser can open an image, inspect prelabels, add/delete/resize a box, save, and reopen it; 80 task image URLs all return successfully; executor remains off; credentials/secrets are absent from git and process output. Write `output/offline_tasks/LABEL_STUDIO_VPS_ACCESS.md` untracked and `analysis/label_studio_vps_deployment.md` without credentials.
  QA scenarios: curl anonymous/auth checks plus real browser desktop/mobile on the public URL; verify VPS memory and fable dashboard health before/after. Telegram completion notification is deferred until the exposed bot token is revoked and a rotated token plus valid group/channel chat_id are available through environment variables. Evidence `.omo/evidence/task-4a-label-studio-vps.md` plus screenshots.
  Commit: Y | `Deploy public Label Studio review workflow`

- [x] 4B. Benchmark open-source architectures and pilot the best pattern
  What to do / Must NOT do: Research current official repos/docs across execution/backtesting (Freqtrade, NautilusTrader, Lean, vectorbt/Qlib), reproducibility (DVC, MLflow), visual-data quality (Label Studio, FiftyOne, CVAT, SAHI), and lightweight orchestration. Map architecture patterns to measured fable gaps; rank by value, integration cost, dependency/license risk, and reversibility. Do not replace the stack or install multiple heavy frameworks on popularity alone.
  Parallelization: Wave 1 | Blocked by: current task completion | Blocks: dynamic follow-up and 10
  References: current `docs/ARCHITECTURE.md`, strategy/YOLO/VPS evidence, official GitHub repositories and documentation only for technical claims.
  Acceptance criteria: `analysis/oss_architecture_benchmark.md` contains source URLs, access dates, licenses, pinned SHAs where code patterns are used, adopt/adapt/reject decisions, and a ranked top three. Implement at least one isolated pilot with objective baseline comparison; keep it only if evidence is positive. No judgment holdout.
  QA scenarios: reproduce the selected pattern on a small real fable artifact; compare correctness, runtime, maintenance cost, and output against the existing path. Evidence `.omo/evidence/task-4b-oss-benchmark.md`.
  Commit: Y | `Benchmark OSS patterns for fable`

- [x] 5. Verify and harden P2.5 locally
  What to do / Must NOT do: Run the focused ops suites and full relevant pytest. Start a loopback-only server with a temporary non-repo token and `ENABLE_JOB_EXECUTOR=0`; verify fail-closed auth, authenticated reads, rejected job execution, model/data hubs, and browser tabs. Fix only reproduced defects with tests.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 6, 9, 10
  References: `docs/P2_5_PHASE01_README.md`, `docs/P2_5_PHASE2_README.md`, `docs/P2_5_PHASE3_README.md`, `tests/test_ops_phase01.py`, `tests/test_ops_jobs_phase2.py`, `tests/test_ops_phase3_hubs.py`, `tests/test_ops_data_model_hub.py`.
  Acceptance criteria: focused suites pass; auth yields expected 401/503/200 and POST execution rejection; browser tabs have no console errors or 390px overflow; executor remains off.
  QA scenarios: curl plus Playwright on an unused `127.0.0.1` port. Evidence `.omo/evidence/task-5-p25-local.md` plus screenshots.
  Commit: N when green; otherwise Y with one focused fix.

- [x] 6. Put a redacted full-pipeline view on the VPS
  What to do / Must NOT do: Add a read-only pipeline status surface showing data freshness, detection/YOLO status, judgment model fingerprint, backtest evidence label, forward counts/PnL, scheduled jobs, and deployment state. Public output must be coarse and redacted; controls remain behind ops auth and VPS executor remains `0`. Deploy through the existing script and verify the actual VPS in a browser.
  Parallelization: Wave 1 | Blocked by: 5 | Blocks: 9, 10
  References: `src/webapp/server.py`, `src/webapp/static/index.html`, `src/webapp/static/app.js`, `scripts/deploy_vps.sh`, `docs/P2_5_PHASE3_README.md`, VPS `http://103.214.174.58:8642`.
  Acceptance criteria: API tests prove no secrets/absolute local paths/write actions; localhost and VPS show every pipeline stage, forward sample caveat, YOLO gate, and executor-off state; desktop and 390px screenshots pass with no console errors.
  QA scenarios: curl API schema plus Playwright localhost/VPS. If SSH/deploy fails, preserve local evidence and report the exact external blocker. Evidence `.omo/evidence/task-6-vps-pipeline.md` plus screenshots.
  Commit: Y | `Show end-to-end pipeline status in the ops console`

- [ ] 7. Finalize the compliant E2.1b detector evaluation
  What to do / Must NOT do: After the existing E2.1b process exits, evaluate fixed YOLO val, export predictions, run consistency at the existing definition, and classify hard cases. Compare E2.1 diagnostic with E2.1b and name HSV-zero as the only training variable. Never run the hardcoded finalize helper or start/stop training.
  Parallelization: Wave 2 | Blocked by: E2.1b exit, 1 | Blocks: 8, 10
  References: `/Users/zhangzc/fable-trading-codex/output/offline_tasks/yolo_e21b_hsv0_20260710.log`, E2.1b run directory, `analysis/p2a_e21_train_report.md`, `scripts/export_yolo_preds_for_audit.py`, `src/detection/consistency_check.py`, `scripts/fiftyone_label_audit.py`.
  Acceptance criteria: `analysis/p2a_e21b_hsv0_report.md` contains command/config, P/R/mAP50/mAP50-95, consistency, hard-case categories, same-table comparison, gate result, and honesty section; all forbidden augmentation args are zero.
  QA scenarios: parse and reconcile metrics/prediction counts; missing artifacts produce a truthful failure, never a fabricated metric. Evidence `.omo/evidence/task-7-e21b-final.txt`.
  Commit: Y | `Report compliant YOLO E2.1b results and hard cases`

- [ ] 8. Run one fixed SAHI benchmark on E2.1b
  What to do / Must NOT do: Use the previously approved isolated SAHI environment, never the main `.venv`. Predeclare `640x371` slices, `0.2` overlap, and the same confidence/IoU definitions before evaluating. Benchmark the same detector val and report accuracy plus latency. Do not tune slice/conf/IoU after seeing results and do not call custom SAHI metrics official Ultralytics validation unless the evaluator is mathematically identical.
  Parallelization: Wave 2 | Blocked by: 7 | Blocks: 10
  References: E2.1b best weights and val manifest, existing isolated SAHI/FiftyOne setup, `scripts/export_yolo_preds_for_audit.py`, `src/detection/consistency_check.py`.
  Acceptance criteria: reproducible command/script, environment record, fixed parameters, mAP50/mAP50-95 or clearly named equivalent, consistency, runtime/image, and baseline comparison in `analysis/p2a_e21b_sahi_report.md`.
  QA scenarios: run a tiny sample first, then full val only if counts/coordinates reconcile; OOM or dependency failure is recorded without touching main dependencies. Evidence `.omo/evidence/task-8-sahi.txt`.
  Commit: Y | `Benchmark fixed SAHI inference for E2.1b`

- [x] 9. Exercise the complete daily workflow and monitoring path
  What to do / Must NOT do: Run the safe local sequence data status -> forward main/shadows -> digest dry-run -> pipeline API, prove rerun idempotency, and add read-only anomaly flags for stale data, failed jobs, low forward sample count, model fingerprint mismatch, and YOLO gate state. Do not send alerts, fetch a new source, or execute VPS jobs.
  Parallelization: Wave 2 | Blocked by: 4, 6 | Blocks: 10
  References: `scripts/update_okx.py`, forward scripts, `scripts/daily_digest.py`, pipeline endpoint from Todo 6, existing scheduler configuration.
  Acceptance criteria: one evidence bundle connects exact commands to UI values; second run creates no duplicate forward rows; anomaly tests cover both healthy and stale/fingerprint mismatch states; VPS still reports executor off.
  QA scenarios: CLI, API, and real browser values agree. Evidence `.omo/evidence/task-9-e2e-workflow.md`.
  Commit: Y only if monitoring code changes | `Add pipeline health anomaly indicators`

- [ ] 10. Close the two-day run with a truthful system verdict
  What to do / Must NOT do: At the forty-eight-hour deadline, or only after a fresh audit finds no further meaningful safe work, update `HANDOFF.md`, `PROJECT_STATUS.md`, and `NEXT_STEPS.md` for verified outcomes. Write `analysis/grok_2day_final_report.md` covering strategy evidence, current forward PnL/sample size, YOLO metrics, SAHI result, VPS URL/screenshots, tests, failures, weak work, commits, and remaining gates. Mark runtime status `FINAL_COMPLETE`; do not merge main.
  Parallelization: Wave 3 | Blocked by: 1-9 or deadline | Blocks: final wave
  References: all `.omo/evidence/`, branch git log, current reports and runtime state.
  Acceptance criteria: every claim links to a commit/test/artifact; distinguish historical positive backtest, short forward observation, and unproven future return; branch is pushed and tracked files are clean; main dirt remains untouched.
  QA scenarios: `git status`, `git log origin/main..HEAD`, full focused tests, local/VPS smoke, metric-file parse, and secrets scan agree with the report. Evidence `.omo/evidence/task-10-final-audit.txt`.
  Commit: Y | `Summarize two-day trading system hardening`

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit: verify every todo/evidence/commit; reject holdout reads, validation retuning, future-profit claims, or live-state mutation.
- [ ] F2. Code quality and security review: inspect `origin/main..codex/grok-2day`, learning notes, tests, secrets/path exposure, dependency drift, and VPS executor state.
- [ ] F3. Real manual QA: rerun forward/shadow idempotency, local auth/API/browser, VPS pipeline, and parse final E2.1b/SAHI artifacts.
- [ ] F4. Scope fidelity: prove no ACTIVE/threshold/blacklist/TP-SL/cost change, no duplicate training, no main push/merge, and clear historical-vs-forward evidence labels.

## Commit strategy

- One atomic commit per completed todo that changes tracked files; audits with no changes produce evidence only.
- Detect and follow current English imperative/status message style; implementation and its direct tests stay together.
- Before every commit: exact-path staging, staged diff/stat/check, relevant tests, and secrets scan.
- After every commit: push `codex/grok-2day`; never push/merge/rewrite `main`; never force push.
- `.omo/runtime/`, `.omo/evidence/`, logs, data, datasets, runs, local tokens, and generated review media remain uncommitted.

## Success criteria

- Grok runner survives restarts and quota failures for about forty-eight hours, continuing after success and imposing a full five-hour backoff after quota, network, or task failure.
- Hourly local pulse continues without model tokens; no duplicate training/fetch/finalize process is created.
- HSV fix exists on the worker baseline with passing regression tests and runtime-zero evidence.
- Strategy report shows reproducible pre-holdout fold stability without parameter search, and forward main/challenger logs remain idempotent and honestly sample-limited.
- P2.5 focused/full tests pass; localhost and VPS expose a redacted end-to-end pipeline view with executor off.
- E2.1b receives a truthful official report; fixed SAHI inference is compared separately with accuracy and latency evidence.
- `codex/grok-2day` is pushed with a clean tracked worktree and a Chinese final report that says exactly what is profitable historically, what is positive or negative prospectively, and what remains unproven.
