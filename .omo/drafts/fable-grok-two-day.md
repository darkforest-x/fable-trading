---
slug: fable-grok-two-day
status: executable
intent: clear
pending-action: launch Grok runner
approach: One isolated Grok worktree executes a durable queue every five hours for about forty-eight hours; Codex wakes at the same cadence only to verify, repair the runner, and report.
---

# Draft: fable-grok-two-day

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
<!-- id | outcome (one line) | status: active|deferred | evidence path -->

| id | outcome | status | evidence path |
|---|---|---|---|
| C1 | A restartable Grok runner works for about forty-eight hours with five-hour backoff | active | `.omo/plans/fable-grok-two-day.md` |
| C2 | All code changes stay isolated on `codex/grok-2day` and are pushed atomically | active | `git worktree list`; `AGENTS.md:14-19` |
| C3 | Forward tracking, data updates, and daily digest remain healthy | active | `HANDOFF.md:90-103`; `AUTONOMOUS_CHARTER.md:21-22` |
| C4 | E2.1 is recorded honestly and the already-running E2.1b is evaluated without duplicate training or forbidden augmentation | active | `analysis/p2a_e21_train_report.md:1-26`; commit `46fadec`; `/Users/zhangzc/fable-trading-codex/output/offline_tasks/yolo_e21b_hsv0_20260710.log` |
| C5 | P2.5 Phase 0-3 receives regression and real-surface QA without enabling VPS execution | active | `NEXT_STEPS.md:333-373`; `HANDOFF.md:136-139` |
| C6 | A final Chinese handoff names completed, failed, deferred, and owner-gated work | active | `AGENTS.md:29-43`; `PROJECT_STATUS.md:68-101` |

## Open assumptions (announced defaults)
<!-- Record any default you adopt instead of asking, so the user can veto it at the gate. -->
<!-- assumption | adopted default | rationale | reversible? -->

| assumption | adopted default | rationale | reversible? |
|---|---|---|---|
| Token budget | One Grok invocation every five hours; Codex heartbeat every five hours | User explicitly requested minimum token use and five-hour recovery | yes |
| Grok model | `grok-4.5`, low reasoning, no subagents, no web search, no cross-session memory | Mission file carries full context; minimizes repeated reasoning and auxiliary tokens | yes |
| Git integration | Push only `codex/grok-2day`; do not merge or rewrite `main` unattended | Main worktree is dirty and has active background processes | yes |
| Experiment policy | Do not stop or duplicate the already-running E2.1b; all later threshold/label/model experiments are proposal-only | Preserves single-variable and owner-gate rules | yes |
| Test policy | TDD for bug fixes; tests-after for audits/docs; real CLI/API/browser QA when a surface changes | Matches repository and agent quality rules | yes |

## Findings (cited - path:lines)

- `AGENTS.md:8-19` forbids holdout reads, random splits, multi-variable experiments, and all YOLO HSV/flip/mosaic/mixup augmentation.
- `AGENTS.md:48-60` makes holdout, thresholds, TP/SL, ATR, and cost owner-gated and requires a learning note for non-trivial conclusions.
- `output/offline_tasks/AUTONOMOUS_CHARTER.md:17-35` prioritizes forward health, data continuity, YOLO completion, P2.5 regression, and engineering hygiene.
- `analysis/p2a_e21_train_report.md:8-26` records E2.1 mAP50 0.8503 and consistency 0.5042, both failed, with no holdout use.
- Commit `46fadec` fixes historical `hsv_s/v=0.05`; current main still contains the non-zero values and needs a clean port.
- `PROJECT_STATUS.md:46-65` says the only hard trading gate is forward data near 100 samples; P2.5 Phase 0-3 is complete and the VPS executor must stay off.
- Grok CLI 0.2.93 is logged in with default `grok-4.5`; session registry currently returns no recoverable sessions, so file-backed fresh invocations are safer than `--continue`.

## Decisions (with rationale)

1. Use a fresh Grok invocation per cadence, reading the same plan/status files; never depend on Grok session history.
2. Run the worker in `/Users/zhangzc/fable-trading-grok-2day` on `codex/grok-2day`; inspect runtime artifacts from the main/codex worktrees by absolute path.
3. On every wake, execute at most one atomic engineering item plus health checks, commit/push it, update status, and exit.
4. A non-zero Grok exit, quota message, missing network, or approval request causes a five-hour sleep rather than rapid retries.
5. Never start duplicate E2.1/E2.1b training. After E2.1b, only produce metrics, consistency, hard-case classification, and an owner proposal.
6. Codex performs sparse independent verification every five hours and does not redo Grok work.
7. Keep the existing hourly local pulse because it uses no model tokens; only Grok/Codex model invocations use the five-hour cadence.
8. Never execute `scripts/_yolo_e21_finalize.sh` from the worker branch because it hardcodes the main worktree and `git push origin main`.
9. Treat judgment holdout and consumed trading validation windows as forbidden; YOLO detector val is allowed only for detector reporting.
10. Use an atomic lock directory and PID liveness check so only one Grok invocation can run at once; never kill active training/fetch screens.

## Scope IN

- Port HSV-zero training compliance and honesty notes onto the current main baseline branch.
- Monitor forward main/H1 ledgers, daily update/digest, stale/part files, and active screens.
- Validate the already-running E2.1b completion, runtime args, YOLO detector val metrics, consistency, and FO hard cases.
- Run P2.5 tests and local API/browser smoke; fix only reproducible defects.
- Keep durable status, evidence, commit log, blocked list, and final two-day report.

## Scope OUT (Must NOT have)

- No judgment holdout or consumed trading-validation-window reevaluation; YOLO detector val is allowed only for its fixed detector report.
- No threshold, TP/SL, ATR, cost, frozen ACTIVE model, blacklist, or 8-55 YOLO decision changes.
- No real-money API key, demo key fabrication, live orders, or secret/token commits.
- No VPS `ENABLE_JOB_EXECUTOR=1`, force push, destructive cleanup, dependency/framework migration, or duplicate training.
- No merging to `main`; worker pushes only its isolated branch.

## Open questions

None. The user explicitly authorized two-day unattended execution, Grok delegation, five-hour recovery, and minimum-token operation. Owner-gated discoveries are recorded and skipped.

## Approval gate
status: approved-by-user-2026-07-10
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
<!-- That durable record is the loop guard: on a later turn read it and resume at the gate instead of re-running exploration. -->

## Review receipt

- Metis `019f4a4d-a82a-7ab1-9a85-248cf08ab248`: completed read-only review; all ten findings were folded into the final plan, including hardcoded-main script ban, E2.1b no-duplicate rule, exact P2.5 QA, lock policy, cadence split, and compact-read allowlist.
