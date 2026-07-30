# causal-direction-profit-optimization - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** A new causal image model that sees only information available at signal time
and predicts long, short, or no-trade, plus an economic comparison against a numeric baseline.

**Why this approach:** The current detector learns only where moving averages are dense. Direction
and profitability must be supervised explicitly, without showing the future candles visible in
hand-picked screenshots.

**What it will NOT do:** It will not touch the frozen trading strategy, search thresholds, read the
holdout, enable live/VPS execution, or claim that a validation result proves future profit.

**Effort:** Large
**Risk:** Medium - image generation and training are long-running, while experimental leakage must
remain impossible.
**Decisions to sanity-check:** Fixed three-class label contract; fixed 320px/20-epoch nano model;
argmax evaluation without threshold tuning.

Your next move: Execution was approved by the owner on 2026-07-11. Full execution detail follows.

---

> TL;DR (machine): Large/medium-risk additive causal YOLO classification experiment with fixed
> direction labels, numeric baseline, economic val evaluation and no promotion.

## Scope
### Must have
- Causal MA206 chart images ending exactly at each signal bar.
- Fixed long/short/no_trade outcomes from existing TP5/SL2 h72 labelers.
- Strict chronological train/val split with purge and a hard pre-holdout assertion.
- YOLO11 classification wrapper with all color/time/geometry augmentation disabled.
- Same-manifest numeric multiclass baseline and fixed-cost economic comparison.
- Reproducible report, tests, sample visual QA, commit and branch push.
### Must NOT have (guardrails, anti-slop, scope boundaries)
- No holdout rows in images, manifests, training, prediction or metrics.
- No change to ACTIVE, MA thresholds, q90, TP/SL, h72, cost assumptions or forward logs.
- No future candle in any model input; labels alone may use the fixed future horizon.
- No duplicate training while E2.1b is running; direction training waits for natural exit.
- No threshold/confidence/augmentation search and no result-dependent second run.
- No main-venv dependency change, real orders, VPS executor, Telegram token or main push.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD with pytest; failing causal/temporal/label/config/economic-contract tests
  precede implementation.
- Evidence: `.omo/evidence/task-*-causal-direction-profit-optimization.*`

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- Wave 1: Todos 1 and 2 define contracts and independent pure evaluators.
- Wave 2: Todo 3 builds/render-audits the dataset after Todo 1.
- Wave 3: Todo 4 trains only after E2.1b naturally exits and Todo 3 passes.
- Wave 4: Todo 5 evaluates/report; Todo 6 conditionally registers discovery evidence.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 3 | 2 |
| 2 | none | 5 | 1 |
| 3 | 1 | 4, 5 | none |
| 4 | 3, E2.1b exit | 5 | none |
| 5 | 2, 3, 4 | 6 | none |
| 6 | 5 | final wave | none |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. Define the causal direction manifest contract
  What to do / Must NOT do: Add pure typed helpers that combine existing expanded long/short
  candidates, enforce an 18-bar cross-direction gap, call the frozen long and short TP5/SL2 h72
  labelers, assign exactly long/short/no_trade, and split globally by time with purge. Filter each
  source frame before indicator/feature construction and assert every signal precedes
  `HOLDOUT_START - purge`. Do not render or train yet.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 3
  References: `src/judgment/candidates.py`, `src/judgment/labeling.py`,
  `src/judgment/train.py:36-73`, `src/data/loader.py`, `tests/test_short_side.py`.
  Acceptance criteria: unit tests prove all three labels, cross-direction dedupe, global chronological
  split, train/val purge, and hard rejection of holdout timestamps.
  QA scenarios: `pytest tests/test_causal_direction_dataset.py -q`; failure fixture inserts a signal at
  holdout start and must raise before manifest output. Evidence
  `.omo/evidence/task-1-causal-direction-profit-optimization.txt`.
  Commit: Y | `Add causal direction manifest contract`

- [x] 2. Add fixed economic and numeric baselines
  What to do / Must NOT do: Add pure evaluation functions for a predicted long/short/no_trade class:
  choose the corresponding existing realized return, treat no_trade as no position, and report trade
  coverage, win rate, gross/net per trade, PF and max drawdown at fixed 0.06%/0.2%/0.3% costs. Add a
  same-manifest LightGBM multiclass baseline using existing causal FEATURE_COLUMNS. No threshold
  search, no holdout, no ACTIVE write.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 5
  References: `src/judgment/features.py`, `src/judgment/train.py`,
  `src/backtest/maker_val_sim.py`, `analysis/ma206_profitability_diagnosis.md`.
  Acceptance criteria: tests reconcile hand-calculated long/short/no_trade trades, PF, DD and fixed
  costs; numeric baseline accepts train/val only and emits class probabilities in manifest order.
  QA scenarios: `pytest tests/test_direction_economics.py -q`; malformed class and non-finite return
  fixtures fail loudly. Evidence `.omo/evidence/task-2-causal-direction-profit-optimization.txt`.
  Commit: Y | `Add fixed direction economics evaluator`

- [ ] 3. Build and visually audit the causal image dataset
  What to do / Must NOT do: Add a CLI that materializes the approved manifest and renders exactly
  200 bars ending at the signal bar into `train|val/{long,short,no_trade}`. Output an immutable CSV
  manifest with source/symbol/signal time/split/class/candidate sides/long+short outcomes+returns/image
  path and a summary JSON. Never render holdout. Never render a candle after signal time.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 4, 5
  References: `src/detection/render.py`, `src/detection/build_dataset.py`, Todo 1 helpers.
  Acceptance criteria: future-row mutation produces byte-identical images; manifest/image counts and
  class folders reconcile; max rendered candle time equals signal time; train/val ranges pass purge.
  QA scenarios: build a 30-image smoke pack, inspect a 3x3 class montage with `view_image`, then build
  full pre-holdout dataset. Evidence `.omo/evidence/task-3-causal-direction-profit-optimization.md`
  plus montage PNG.
  Commit: Y | `Build causal MA206 direction images`

- [ ] 4. Train one fixed YOLO direction classifier
  What to do / Must NOT do: Only after PID 37441 exits naturally, train exactly one
  `yolo11n-cls.pt` run on Todo 3 using imgsz=320, epochs=20, batch=32, patience=8, MPS/CPU auto,
  seed=42. Set flips, HSV, translate, scale, erasing and auto augmentation to zero/off. Do not resume,
  sweep, duplicate or modify the main `.venv`; model download is allowed through existing Ultralytics.
  Parallelization: Wave 3 | Blocked by: 3 and E2.1b natural exit | Blocks: 5
  References: `src/detection/train.py`, Ultralytics classification task in installed 8.4.89.
  Acceptance criteria: args artifact proves fixed config and forbidden augmentation zeros; run exits
  naturally with best.pt/results.csv; training log has no traceback/non-finite metrics.
  QA scenarios: config-only regression test, then actual CLI run; missing dataset must fail before a
  training process starts. Evidence `.omo/evidence/task-4-causal-direction-profit-optimization.txt`.
  Commit: Y before long run | `Add fixed causal direction training entrypoint`

- [ ] 5. Evaluate economic value and write the decision report
  What to do / Must NOT do: Run best.pt on every val image once, map model.names safely, calculate
  classification metrics/confusion plus Todo 2 economics, and compare image model vs numeric baseline
  and deterministic candidate-side baseline on identical rows. Use argmax only. Report failures and
  screenshot-specific interpretation. No holdout or result-dependent rerun.
  Parallelization: Wave 4 | Blocked by: 2, 3, 4 | Blocks: 6
  References: Todos 2-4, `analysis/ma206_profitability_diagnosis.md`, user screenshots context.
  Acceptance criteria: `analysis/causal_direction_profit_report.md` includes counts/time ranges,
  confusion, fixed-cost PF/net/DD, baselines, latency, leakage assertions, honesty and gate verdict.
  QA scenarios: reconcile prediction count to val manifest and manually trace nine montage samples
  from signal candle to assigned class/outcome. Evidence
  `.omo/evidence/task-5-causal-direction-profit-optimization.md`.
  Commit: Y | `Report causal YOLO direction economics`

- [ ] 6. Register only evidence-supported follow-up
  What to do / Must NOT do: If val net@0.2% is positive, PF>=1.3 and at least 100 predicted trades,
  register the frozen artifact as a discovery-only challenger without changing ACTIVE or forward logs;
  otherwise record the failed hypothesis and leave runtime untouched. Never tune to cross the gate.
  Parallelization: Wave 4 | Blocked by: 5 | Blocks: final wave
  References: `src/judgment/frozen.py`, `src/judgment/shadow_registry.py`, project holdout/ACTIVE law.
  Acceptance criteria: deterministic branch based solely on predeclared gate; fingerprinted artifact
  or explicit rejected record; ACTIVE SHA and both forward ledger SHAs unchanged.
  QA scenarios: model-hub read-only API shows discovery/rejected status and VPS executor remains off.
  Evidence `.omo/evidence/task-6-causal-direction-profit-optimization.md`.
  Commit: Y when tracked status changes | `Register causal direction experiment verdict`

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [ ] F3. Real manual QA
- [ ] F4. Scope fidelity

## Commit strategy

- One atomic commit per todo with exact-path staging and push to `codex/grok-2day`.
- Dataset images, weights, logs and raw predictions remain untracked; reports, tests and model metadata
  are tracked. Never commit credentials or absolute local paths in public payloads.

## Success criteria

- Causal input invariance is proven by future-row mutation tests and rendered-time assertions.
- No holdout timestamp exists in manifest/images/metrics; no forbidden training augmentation is nonzero.
- Image and numeric models are compared on identical val rows with fixed argmax and fixed costs.
- The result is reported even when negative, with no threshold rescue and no ACTIVE/live mutation.
- Relevant tests/full suite pass, visual montage is inspected, every commit is pushed.
