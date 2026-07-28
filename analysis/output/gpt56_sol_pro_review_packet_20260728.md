CONTEXT_PACKET_V1 — FABLE_TRADING_STRICT_PROJECT_REVIEW

```json
{
  "task_id": "gpt56-sol-pro-consult-20260728-144105",
  "sentinel": "GPT56_SOL_PRO_RESULT_20260728_144105",
  "task_type": "architecture_review",
  "context_strategy": "problem_first_full_context_plus_real_code_bundle",
  "credential_status": "no_executable_credentials_scanner_passed",
  "context_hash": "e19a8d03efc14b13e2fd166497a950d090dca6a20ac64d23fd29fd494a9c7518",
  "evidence_bundle": "gpt56_sol_pro_project_bundle_20260728.md",
  "required_output": [
    "reasoning_brief",
    "direct_judgment",
    "biggest_flaw",
    "ranked_required_changes",
    "implementation_and_test_plan",
    "adoption_decision"
  ]
}
```

## TASK

Strictly review the attached `fable-trading` repository evidence and decide what Codex should implement now. This is a real-money-capable trading system, so prioritize fail-closed safety, causal validity, and contract integrity over feature expansion.

The user explicitly requested this loop: GPT 5.6 Sol Pro reviews the project; Codex owns local verification, implementation, and testing; then Sol Pro re-reviews the actual diff and test evidence and Codex iterates.

For this first pass, identify the smallest high-leverage implementation that is both:

1. supported by the attached evidence; and
2. allowed without owner approval under the constraints below.

Do not suggest consuming holdout data or changing trading parameters. Do not assume that code not present in the attachment exists.

## BACKGROUND

The project tests a two-layer 15-minute crypto strategy:

- YOLO detects a moving-average-density launch shape at/near the live market tip.
- LightGBM ranks detected candidates.
- A forward log feeds a real-money-capable OKX executor with TP/SL/timeout controls.

The repository evolved rapidly over about three weeks. The current honest production state is `detector=none`: no detector is approved for live trading, so the pipeline is supposed to update data and resolve already-open rows but discover no new candidates. Historical detectors that only worked after the fact were removed. Promotion, ACTIVE/frozen switches, forward-log clearing, actual trading changes, position changes, and API-key changes all require explicit owner approval.

The newest offline audit (attached) found:

- 25,602 short-side detector candidates earned +26.91 bp gross per trade in the development window.
- A matched random short control (same coin, month, ATR bucket, same barrier) earned most of that directional drift.
- Detector incremental selection value was +8.97 bp versus a fixed 10 bp round-trip cost, before slippage.
- The owner gold labels look far stronger (+107 bp matched excess), but only 2/499 were drawn with at most two future bars visible. Therefore causal learnability of the target is unknown, not disproven.
- The decisive research step is owner labeling of live-tip-only images; that requires the owner and is not an autonomous Codex implementation task.

No holdout was read during this audit. The current workspace contains uncommitted audit artifacts that belong to the user and must be preserved.

## USER_INTENT

The user wants a rigorous outside review, not generic advice. They want Codex to implement and test defensible fixes and to iterate after your re-review.

Success for this consultation is a prioritized, evidence-grounded engineering decision. A good answer clearly separates:

- a code defect or unsafe runtime path Codex may fix now;
- a research unknown requiring owner labels or owner approval;
- a stale-documentation issue;
- a tempting but unjustified strategy/model change that must not be made.

## NON-NEGOTIABLE CONSTRAINTS

1. Do not read or recommend reading holdout data (`signal_time >= 2026-05-04`) without explicit owner approval. The attached tests and code inspection do not consume it.
2. No random train/validation splits and no feature look-ahead.
3. Do not change thresholds, TP/SL multiples, ATR floors, the 10 bp cost assumption, freshness gates, position sizing, ACTIVE/frozen config, model promotion, forward-log contents, or live orders.
4. No live detection path may produce only-after-the-fact signals. Live windows are tip/tip-1/tip-2 only.
5. With no validated detector, candidate discovery must idle honestly (`detector=none`).
6. VPS is the sole writer of live K-line and forward-log data.
7. Each non-trivial fix must have tests and a concise learning note.
8. Existing dirty-worktree changes are user-owned and cannot be reverted or overwritten.

## LOCAL_JUDGMENT BEFORE CONSULTATION

Codex's current best judgment is that the highest-priority autonomous change is to make candidate-source selection fail closed and remove the legacy automatic rule fallback from the production pulse.

Evidence:

- `scripts/forward_pulse.sh` lines 4-5 say that if ultralytics/torch is missing, the pulse falls back to rule candidates “so the clock still moves.”
- Lines 17-22 actually export `FABLE_CANDIDATE_SOURCE=rules` when `import ultralytics` fails.
- Lines 55-58 still invoke the executor after the scan.
- `src/judgment/forward_types.py` lines 24-27 explicitly allow `FABLE_CANDIDATE_SOURCE=rules` as a VPS fallback.
- `src/judgment/forward_scan.py::_rule_candidate_indices` is a genuine candidate generator; it does not idle.
- This directly conflicts with the repository iron rule: no verified detector means no discovery.
- The Python path already has the desired behavior when source is YOLO but weights are absent: it catches `FileNotFoundError`, reports `detector=none`, and returns no new candidate indices while still resolving tracked rows.

Codex proposes, subject to your critique:

1. In the live pulse, never switch to rules. Force/validate `FABLE_CANDIDATE_SOURCE=yolo`; if ultralytics is unavailable, log `detector=none` and skip discovery safely while preserving data refresh and tracked-row resolution if the Python stack can run. If the Python module itself cannot import, the pulse must fail before executor dispatch rather than trade from stale/new legacy candidates.
2. Validate candidate-source values centrally so typos or unsupported sources fail loudly. Decide whether explicit `rules` should remain possible only in offline research code, never the production forward entrypoint.
3. Add regression tests that prove the production pulse cannot export/use rules and that detector absence yields zero discovery.
4. Update comments/docs that still advertise automatic rules fallback or “live 6-window”; actual live schedule is tip/tip-1/tip-2.

A second contract gap exists but may not be the right first implementation:

- Current research direction is short-only.
- `ForwardRecord` has no trading direction field.
- `src/execution/executor.py::open_one` is intentionally long-only: entry `buy`, bracket `sell`, hedge `posSide=long`.
- The active frozen model is still historical v11 and no short model is promoted, so this is not an active mis-trade today.
- Codex's view is to add a fail-closed promotion/compatibility guard before any short cutover, but not to implement live short order support without explicit owner authorization because that expands real-money behavior.

The newest matched-control audit also shows a methodology gap. The project rules now require a matched control in every directional-strategy result table. Codex is uncertain whether to integrate that control into `src/judgment/train.py` immediately or keep it as a separate diagnostic until the matching design and dependencies are stabilized. Please distinguish required safety implementation from research-framework refactoring.

## EVIDENCE

Attached file: `gpt56_sol_pro_project_bundle_20260728.md`.

It is a generated text bundle containing the actual current contents of 22 files, including:

- project rules and architecture (`AGENTS.md`, `README.md`, `docs/ARCHITECTURE.md`, `docs/DOC_MAP.md`);
- the newest matched-control/gold-label audit and its two learning notes;
- the two exact diagnostic scripts used for the audit;
- production pulse, candidate-source, YOLO scheduling, frozen artifact, forward scanning, training, executor, and config code;
- targeted tests for tip detection, tip-edge filtering, forward tracking, and portfolio/execution behavior.

Bundle size: 180,128 bytes. The bundled safety scanner reported zero high-severity credential findings. It found only five warnings for local absolute paths in diagnostic scripts; no executable credentials were included.

Current test baseline before any implementation:

```text
209 passed, 2 skipped, 14 warnings in 13.03s
```

The skipped tests are existing skips, not introduced by this task.

## ATTEMPTS_SO_FAR

No code was changed yet for this consultation. Codex performed read-only inspection and ran the full local test suite.

The repository already contains several historical fixes for fail-closed behavior, including:

- missing YOLO weights produce `detector=none` inside Python;
- invalid long-side barriers refuse entry;
- freshness is gated at 30 minutes;
- live YOLO schedule was reduced to tip/tip-1/tip-2 to stay under the pulse budget.

The unresolved inconsistency is that the shell production entrypoint can bypass the intended missing-detector idle behavior by changing the candidate source to rules before Python starts.

## OPTIONS

### Option A — safety fix first (Codex preference)

Remove/forbid production rule fallback, validate the candidate source, add regression tests, and update stale comments. Do not touch strategy behavior, holdout, models, or live state.

### Option B — research methodology first

Refactor matched controls into the training/evaluation framework. This may improve future honesty but does not close the current fail-open runtime path and requires careful design around overlapping entries, matching cells, and missing strata.

### Option C — implement short execution now

Add side to forward records and make executor trade both long and short. This solves a future contract need but changes real-money-capable behavior before any short artifact is promoted and likely exceeds current authorization.

### Option D — no code; wait for owner labels

Research-wise, live-tip labels are the decisive unknown. But waiting leaves a known runtime safety contradiction in place.

## RISKS AND UNKNOWNS

- The full deployment unit files and VPS environment are not included; base your judgment on the actual bundled entrypoint and Python contracts only.
- The active detector is currently absent. A fail-closed change should preserve honest idling and must not promote or install a model.
- It is not yet decided whether offline research scripts need `rules` support. Avoid deleting legitimate offline experiment capability if the production boundary can be isolated cleanly.
- The dirty worktree includes audit scripts/reports and project-rule edits. Your proposed diff should be scoped so Codex can avoid trampling them.
- The 8.97 bp matched excess estimate uses overlapping 72-bar events, so its t-stat is optimistic; the key economic comparison 9 bp versus 10 bp does not depend on the t-stat.
- No real slippage estimate exists because there are zero complete live round trips.

## ASK

Act as a strict reviewer and deep reasoning partner. Find the biggest flaw first, then give the strongest revised path. Do not provide generic encouragement. Do not reveal hidden chain-of-thought; instead output a concise reasoning brief with assumptions, decision frame, evidence weighting, strongest counterargument, and tradeoffs.

Please answer all of these explicitly:

1. Is the automatic rule fallback a P0/P1 production defect under the stated doctrine? Why or why not?
2. What is the smallest correct implementation boundary: shell-only, central Python validation, a dedicated `production` mode, or another design?
3. Which exact regression tests should Codex add before claiming the fix?
4. Should Codex touch the short-direction contract now? If yes, limit it to a fail-closed compatibility guard unless you can justify broader live behavior under the authorization constraints.
5. Should matched controls be integrated now, and if so, at what layer without contaminating the one-variable experimental discipline?
6. Rank at most three changes for this iteration. State what Codex must explicitly not change.
7. Provide a concrete acceptance checklist that can be handed back to you with the diff and test results for re-review.

## RETURN_FORMAT

First line must be: GPT56_SOL_PRO_RESULT_20260728_144105

Then use:

1. Reasoning brief: assumptions, frame, evidence, counterargument, tradeoffs
2. Direct judgment
3. Biggest flaw
4. Ranked required changes (maximum three)
5. Exact implementation boundary
6. Exact tests and acceptance checklist
7. What to ignore or defer
8. Final adoption recommendation
