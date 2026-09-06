# V10 verification and delivery

## Outcome and scope

Strict coverage gate rejected, not a profitable strategy accepted.
All251 original mothers retained.154 old complete groups remain the maximum,
61.3545816733% coverage;226 required,72 short,0 allocation gain.
No new trading outcomes,2025+ price materialization,holdout,training,
TradingView,production configuration or live mutation. Goal remains active.

## Frozen generation and failure preservation

- b6e9056: plan/config/reconstruction/support/MILP and synthetic tests committed
  before the first raw development-prefix load.235 relevant tests passed.
- First and only real run ended successfully at2026-09-06 06:58:06.390 UTC.
  Source archive identity767f67c2b0ae5a8c83369a7cb950334e61de09edbb82a0158122c41794eed5ac;
  219551 materialized development rows through2024-12-31 23:55 UTC. The archive's
  whole-file timestamp/hash preflight is distinguished from price materialization.
- All old mother/control/assignment fields and four receipts compared BEFORE
  capacity inference; old allocation feasibility independently checked against
  the rebuilt graph. Complete pre-capacity tables written before solver call.
- started.json, support_frozen.json and summary.json preserve sources, clocks,
  10 pre-solver output hashes and12 final CSV hashes. Existing gz files were not
  rewritten after solving. No failure.json exists. On failure the runner writes
  diagnostics and rethrows, does not lower the goal or rerun another seed.

## Independent saved-graph verifier

Verifier source2129bca; standard library only, no yoyo/pandas/scipy/MILP/price reads.

```bash
.venv/bin/python scripts/verify_hourly_impulse_support_v10.py
```

Actual result:ok=true,mothers251,oldcontrols462,greedy154,maximum154,edges1829;
12output hashes and14source-commit hashes verified. Allocation legal, exact3,
global candidate-time nonreuse and original full-field parity hold. Independent
union-find231 components gives upper154, equal to legal allocation154, so
independent_optimum_proven_for_saved_graph=true. Required226 independently
unattainable. No claim of independent raw-feature replay or graph completeness.
82 synthetic verifier tests passed before reading the real saved graph.

Independent analyst also reconstructed the graph with traversal and checked
97=3missing+19same-slope+1fold-embargo+71cross+3depletion, support availability
0:22/1:36/2:33/3:1/4:2 among the94 search-reached shortages. Three occupied
components each contain2mothers and3/4/4shared candidates, so only1 can match.
Largest allocation swaps one selected mother, rather than adding a mother.

## Test runs, including the failure that was fixed

```bash
.venv/bin/python -m pytest tests/test_hourly_impulse*.py tests/test_verify_hourly_impulse_support_v10.py tests/contracts/test_registries.py -q
```

Final observed result: **1623 passed in24.76s**. This is the hourly-impulse and
registry-contract suite, not a claim that every repository test ran.
Earlier wider run had1619pass/3fail:V8 development/facts andV9 development used
unsupported artifact_type=research_result. Corrected these3 owned metadata rows
to report in279f261; kept their bytes/paths/hashes/results, schema and test
thresholds unchanged. Separate registry rerun16passed; final combined run above
passed. Unrelated ARB registry/HANDOFF hunks and environment changes preserved.

Pure capacity tests include768 exhaustive small graphs plus randomized graph
oracles, order/ID invariance, integer/certificate rejection, no reuse across
direction, greedy failure and bottlenecks. Support tests include missing keys,
source gaps/warmup/current-prior-not-future cross, transfer risk and outcome-
column injection invariance. Nonfinancial null checks replace inapplicable
AUC/win/PF/return permutation metrics; no numerical financial metric fabricated.

## Portable report

Presentation builder16362f6, actual SQLite aggregation/provenance2129bca,
denominator guard279f261. Authored report is new; all V1–V9 reports unchanged.
MD immediately converted through repository md_to_html before final native
packaging, and rebuilt after every narrative correction.

```bash
python3 scripts/md_to_html.py analysis/p1_btcusdtp_hourly_support_v10_20260906.md --out-dir analysis/html
.venv/bin/python -m yoyo.evaluation.hourly_impulse_support_report --markdown analysis/p1_btcusdtp_hourly_support_v10_20260906.md --summary experiments/active/exp-btcusdtp-1h-matching-support-preholdout-20260906-v10/results/summary.json --audit experiments/active/exp-btcusdtp-1h-matching-support-preholdout-20260906-v10/results/mother_audit.csv.gz --output experiments/active/exp-btcusdtp-1h-matching-support-preholdout-20260906-v10/artifact.json
node /Users/zhangzc/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.10-13ceeea1f599/skills/build-report/scripts/deliver_portable_artifact.mjs --input experiments/active/exp-btcusdtp-1h-matching-support-preholdout-20260906-v10/artifact.json --output analysis/html/p1_btcusdtp_hourly_support_v10_20260906.html
```

Actual final package:validation passed,package passed,verification structural_only;
17blocks,1native chart. No compatible Chromium headless-shell installed: no
browser viewports,source menu/dialog,interaction or mobile visual QA. No browser
installed; no parallel HTML/JavaScript chart runtime. The semantic fallback,
required reader roots and exact embedded-artifact equality passed.

First packaging attempt failed for absent actual chart SQL. Fixed by executing
the declared SQLite count query over Python-validated shortage classifications,
with exact upstream mother_audit source, filters and denominator provenance;
did not invent a SQL execution or remove report sections. Final parser preserves
all15 peer sections and literal fences. Analyst reviewed quantities/clock/source
attribution; overbroad heading and non-source-supported history sentence corrected.

## Notebook, precise execution scope

Builder1062c79,29synthetic tests before generating the actual artifact:

```bash
.venv/bin/python -m yoyo.evaluation.hourly_impulse_support_notebook --output analysis/output/btcusdtp_hourly_support_v10_20260906/support_audit.ipynb --check
```

Actual13cells /5code cells executed top-down in ordinary Python, captured actual
stdout. Reverified summary hash6279ce97ac051e168e632291218a697ffc7558db611bcf5e007f23b51bb55440
and3CSV hashes;251mothers/1829edges/462allocation,17orderedstages,231components,
154tightbound and0recoverable. No solver or raw price/return reads.

**Not Jupyter-kernel execution or full nbformat schema validation.** Existing
main/system/tools/FO/LabelStudio/bundled Python runtimes lacked nbformat,
nbclient,ipykernel. No packages installed. Generated minimum nbformat4.5 fields,
IDs and compilation checked; the notebook keeps both missing-validation flags
false. In an already prepared Jupyter environment, separately execute with
nbconvert and run nbformat.validate as documented in its final cell.

## Interpretation boundary and next work

Audit completion is not owner-goal completion. The strict comparison cannot
cover90% of the old251 under unchanged rules. Do not tune seeds,reduce controls,
reuse dates,relax keys,score the new allocation or turn support into a live
entry feature. Full251 same-opportunity policy change and154 supported-group
conditional excess are distinct; neither makes the coverage gate disappear.
One next-mechanism candidate is recorded in NEXT_EXPERIMENT.md, not yet run
or registered as profitable. Independent future validation remains outstanding.
