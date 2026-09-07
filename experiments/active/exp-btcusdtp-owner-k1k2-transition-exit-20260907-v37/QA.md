# V37 QA and release record

## Outcome and authority

Research rejected, overall goal active. No profitable or deployment-ready claim.
Only approved BinanceUSD-M BTCUSDT2023-2024 source, unchanged V36 requests and
controls. No new holdout inspection this experiment. V36's two old-text exposure
incidents remain recorded in V36; this does not retroactively clear that history.
No TV publication, ACTIVE, model training, dependencies, orders, risk/cost changes.

## Freeze and execution

- Runner, config, plan and both research test files committed at457dc62 BEFORE
  the one-shot actual replay. Pre-outcome receipt09:50:54UTC, summary09:51:09UTC.
- Native5 management source210528bars; original63cases108controls36three-control
  groups. All342 arm/cohort outcomes closed and known, no refusals or censoring.
- Baseline both case/control18columns agree with saved V36; IDs/clocks exact,
  numeric tolerance1e-12. New arm changes only exit_mode, flowgate OFF both.
-13saved output hashes rechecked during report preparation and independent
  review. Entry identity/price/stop/risk unchanged; exact20bp cost each event.
- Original27unmatched cases retained; fixed36groups recomputed without redraw.
  Separate single-position schedules independently replayed63vs59,4blocked.
- Runtime builder_paths omits the two test paths. Actual pre-outcome test bytes
  verified against457dc62 in report_data; this is evidence of this run, not an
  automatic test-freeze guard. Frozen config was not retrospectively edited.

## Tests actually run

```bash
.venv/bin/python -m pytest -q tests/test_owner_k1k2_genuine_flow.py tests/test_k1k2_owner_source.py tests/test_owner_k1k2_flow_report.py tests/test_owner_k1k2_exit_clock_audit.py tests/test_k1k2_genuine_flow_alignment.py tests/test_hourly_impulse_k2_matching.py tests/test_hourly_impulse_execution.py tests/test_hourly_impulse_data.py tests/test_owner_k1k2_transition_exit.py tests/test_owner_k1k2_exit_transition_contract.py tests/test_owner_k1k2_transition_report.py
```

Final combined run398passed in6.55s. Frozen transition contract81 and accounting17
passed before replay. Report tests finally3passed; rerun after final SQL edit
3passed in0.27s. Earlier overlapping combinations100/351/397passed must not be
added to398. Synthetic tests cover entry seed, first edge, stop priorities,
unknown data, no future visibility, mirror/scale, joins and SQL denominators.

Full repository suite NOT claimed green or rerun this release. V36 expanded
852pass/4fail result remains historical:2migration hash failures and2holdout
declaration failures, including its incidental-exposure marker. No whitelist
was changed to make that result pass. Unrelated dirty files left untouched.

## Independent review

`REVIEW.md` independently checks saved171two-arm requests,13hashes,20bp economics,
36originalthree-control arithmetic, four halves,36vs27support and63vs59ledger.
Its reproduction command was executed by the reviewer, with no raw-price replay
or p/CI re-sampling. Main report SQL independently materializes folds/mechanisms/
paired rows and distributions. A second read-only review confirmed report
numbers/semantics; corrected44different K1(not independent) and clarified the
extended-loss column is increased holding AND new net loss, not all losses.

## Report-only failures, retained honestly

1. First reporting builder89a5a65 had an unmatched closing parenthesis. It failed
   parsing before report data read/write; economics were not replayed. Fix and
   synthetic SQL tests committedabd0396 before report_data was generated.
2. artifact.json from625c406 was a pre-final prose package. Retained, not final.
3. artifact_reviewed.json froma483fcb incorporated denominator review, but native
   validation rejected its scatter provenance: source lacked an executed SQL
   query. No hosted broken widget was rendered. Final builder0791e19 ACTUALLY
   executes SQLite on the same63saved case comparisons, checks exact equality
   to report_data and records the query; no invented SQL or new economics.
4. Final artifact_final.json from0791e19 is the only canonical delivery. Earlier
   artifacts/receipts remain, never silently overwritten as if they had passed.

## Delivery verification

MD was immediately converted with scripts/md_to_html.py after each final prose
edit. Then canonical artifact validation passed,2datasets/8sources, followed by
portable packaging and exact structural/semantic checks.13blocks,2charts.
Final HTML: analysis/html/p1_btcusdtp_owner_k1k2_transition_exit_v37_20260907.html.
Validation/delivery receipts saved. Verification is STRUCTURAL_ONLY: installed
Chromium headless-shell unavailable. No browser download; mobile/light-dark,
source dialog and interactive viewport behavior NOT verified. Opening/queuing
the file in Codex is not UI acceptance.

Next bounded action is NEXT_ACTION.md, a causal pending-entry feasibility audit,
not a new scored strategy. V37 rejection and all original losses remain frozen.
