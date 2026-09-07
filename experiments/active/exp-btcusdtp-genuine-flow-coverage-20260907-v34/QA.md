# V34 QA and provenance

## Scope and result

- Input-quality audit only: BTCUSDT Binance USD-M, 5m bar opens in
  [2023-01-01,2025-01-01) UTC. No new outcomes/holdout/threshold/cost/exit changes.
- First actual source read followed builder commit49c0d8c. Official CHECKSUM
  receipts frozen before24ZIP reads. All24sources reused existing cache read-only.
- 210528/210528calendar rows;17544/17544hour buckets;0missing/unknown/duplicate/null.
  38knownzero-volume bars are not guaranteed executable quotes.
- Same-bar ordinary price direction vs quote taker delta:40489/209923 opposite,
  not MA Shift colour parity, prediction accuracy, trade win rate or incremental PnL.

## Failed checks and corrections, retained honestly

1. Author second-opinion review found ZIP network errors could abort the whole
   run rather than preserve unknown months; all24real sources used cached ZIPs,
   so this branch did not occur in the real audit. Corrected8aaaa00; synthetic
   network-error test proves unknown is retained. Receipt/cache corruption still
   fails closed. Added verified-checksum successful CSV roundtrip/replay test.
2. New delta identity audit initially scaled float tolerance to the cancellation
   result and verification failed. This is not evidence of bad raw flow.
   Fixed93b556c using8*float64epsilon*(abs(buy)+abs(sell)+abs(delta)), with
   1e8+.01 minus1e8 counterexample and corrupt-delta rejection regression.
   verification.json records the new verifier commit, original49c0d8c unchanged;
   all original monthly fields and output bytes reproduce exactly.
3. Float VWAP checks produced91side flags on84unique bars. Source-first exact
   Decimal audit c90c6da found0exact source violations across210528rows.
   All91flags are float_boundary_only; no source was clipped or excluded.
   The generic unit receipt warning does not imply nonzero unresolved cases.
4. Notebook generator expression syntax error prevented report preparation at
   c90c6da; no report_data was written by that attempt. Fixed54efb7c, added SQL
   import/synthetic denominator smoke. Real SQLite preparation then completed.

## Verification actually performed

```bash
.venv/bin/python -m pytest -q tests/test_binance_flow_coverage.py tests/test_binance_um_flow_archives.py tests/test_binance_um_archives.py tests/test_binance_flow_unit_audit.py tests/test_binance_flow_coverage_report.py tests/contracts/test_registries.py tests/boundaries
```

284passed,13existing Matplotlib/pyparsing deprecation warnings,25.93s.
Same data/source scope reused for validation, not a fresh economic evaluation.
Original parser and legacy parser unchanged. No dependency installs.
Runtime Python3.9.6 / NumPy2.0.2 / pandas2.3.3.

Second opinion was the V33 adapter author's additional review, not independent
third-party certification. They checked code, manifest/summary SHA and24month
arithmetic, and independently recomputed saved91Decimal diagnostic records.
Root separately ran full local source replay and SQLite counts.

## Report and notebook delivery

Audience: product stakeholders. Required-role mapping: title +Executive Summary;
scope/definitions; V33comparison;24month difference chart and exact detail table;
unit findings; nextsteps; furtherquestions; caveats; owner-required reproduction.
One native bar chart with24monthly same-grain fractions, full width, zero baseline,
single-series blue-root marks; columns retain denominators and counts. Initial
all-zero missing-bar chart omitted because it conveys no pattern; exact coverage
stays in tables. Only the presentation changed after observing full completeness,
not population, data predicates, entry rules or economic endpoints.

Canonical artifact validation passed:11markdown blocks+1nativechart,24datasetrows,
10sources, ready input-audit snapshot. All narrative sections preserved.
Standard repository MD->HTML conversion ran immediately after writing the report.
Final portable reader was then produced by the canonical command:

```bash
node /Users/zhangzc/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.10-13ceeea1f599/skills/build-report/scripts/deliver_portable_artifact.mjs --input experiments/active/exp-btcusdtp-genuine-flow-coverage-20260907-v34/artifact.json --output analysis/html/p1_btcusdtp_genuine_flow_coverage_v34_20260907.html
```

`delivery_receipt.json`: validation/package passed; verification structural_only.
No installed Chromium, no browser installed; exact embedded payload, runtime,
reader and semantic fallback roots verified. Desktop/mobile theme/overflow/source
dialog UI QA NOT performed. HTML remains self-contained with shared light/dark
support and semantic chart table. open_in_codex returnedqueued, not displayed.

Notebook has3Python cells, actually executed sequentially in one fresh namespace
with captured outputs and metadata/structure checks. Contract venv lacks nbformat,
nbclient, Jupyter and ipykernel; stdlib scaffold used without newdependencies.
Not Jupyter kernel/UI certification. In an already provisioned matching kernel:
`python -m jupyter nbconvert --execute --to notebook --inplace verification.ipynb`.

## Remaining limitations / next permitted work

Historical archive delivery is retrospective. earliest_available_at is only the
theoretical bar boundary. No observed live arrival or OKX/Binance synchronization
has been validated. Zero-volume cause is not investigated; do not invent outage
attribution. 2023–2024 is repeatedly used development, not fresh acceptance data.

Next: separately freeze event-clock alignment / zero-denominator handling before
any confluence hypothesis or outcome evaluation. K2 counter-flow weakening is a
proposal, no window/threshold selected. No live strategy is eligible; profitability
goal remains unfinished. Initial product goal status was usageLimited; at final
handoff get_goal returned null, then owner-requested create_goal succeeded with
active at2026-09-07 08:09UTC. goal_activation.json records that new active goal;
no usage reset was consumed. Only this final status sentence changed in the
report/artifact; all numerical claims, datasets, block IDs/order and sources
remain exactly as in the first packaged report. Delivery is rerun after correction.
