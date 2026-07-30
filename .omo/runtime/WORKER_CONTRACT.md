# Grok Worker Contract

## Objective

Continuously improve fable-trading through an evidence-driven loop. A finished
task must produce a comparison, a bottleneck diagnosis, and the next testable
hypothesis. The queue is dynamic.

## Batch Loop

Execute up to three atomic iterations in one invocation, stopping earlier only
for a safety boundary, external blocker, quota failure, or insufficient turns.
For each iteration:

1. Verify previous product files, tests, commit, and push.
2. Read only the compact status, next-task packet, and named evidence.
3. Predeclare hypothesis plus objective pass/fail criteria.
4. Implement one bounded change or experiment.
5. Run focused tests and the real CLI/API/browser/model surface.
6. Compare with baseline and record success or failure.
7. Create one atomic commit and push `codex/grok-2day` when tracked files change.
8. Update status, evidence, and `NEXT_TASK.md` before continuing.

## Context Discipline

- Do not reread the full plan, full history, old logs, generated HTML, datasets,
  or broad directory trees unless compact evidence is contradictory or missing.
- Use targeted `rg`, exact files, existing reports, and the latest named runtime
  artifacts. Reuse verified outputs instead of recomputing them.
- Consult `.omo/plans/fable-grok-two-day.md` only when reprioritizing the queue or
  when `NEXT_TASK.md` lacks executable acceptance criteria.
- Keep status/evidence concise. Do not narrate routine tool calls.
- Use `grok-4.5` for all iterations. The owner wants Codex token conservation,
  not Grok model downgrades. Compact context exists to improve focus, not to
  reduce Grok reasoning quality.
- Web/GitHub research is enabled when the task requires current OSS evidence.
  Prefer official repositories/docs, pin commit SHAs for copied patterns, and
  never install a framework merely because it is popular.

## Boundaries

- Work only in `/Users/zhangzc/fable-trading-grok-2day`; push only its branch.
- Keep judgment holdout sealed by default. Never leak/create secrets, place live
  orders, enable VPS job execution, force push, destructively clean user data,
  duplicate healthy jobs, or use helpers that hardcode/push main.
- Preserve the frozen trading champion; new candidates remain paper/shadow until
  complete evidence supports a reversible promotion.
- The Telegram token pasted in chat is compromised and must never be used.
- Existing E2.1b training is observe-only; never duplicate or stop it.

## Current Methodology Findings

- YOLO has per-symbol but not global wall-clock separation; current E2.1b/SAHI
  are diagnostic, not production validation.
- YOLO data is 55 spot symbols with 20/60/120 charts; trading uses the SWAP
  universe with the same SMA/EMA20/60/120 profile. Dataset labels were rewritten without a frozen label fingerprint.
- Label Studio is the public manual box editor; FiftyOne stays local for triage.
