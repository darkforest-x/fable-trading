# V29 verification receipt

## Actual study and independent replay

- Source-first support builder: d305c2c06b5a7b9737a44a2abca1484b64aa9df0.
- One actual support run: started 2026-09-07 03:28:57.498976 UTC; summary 03:29:00.380111 UTC. No support failure receipt exists; no support outputs were overwritten.
- Saved-only independent auditor committed f549dd49b5db2700b4dcdd20707ec6ab70c3318c before execution. `audit.json` status passed.
- Independent standard-library reconstruction: 18,222 saved hourly rows, 995 own-clock contexts, 62 counts, 251 mother groups; all numerical state and support gates reconcile.
- Bracketed 13 input and 5 output hashes; 10 current source identities and 18/12 parent source identities checked. No strategy/pandas import in auditor. Original 248 triples / 744 controls / 3 unsupported mothers retained.
- Scope exclusions remain explicit: no raw5 aggregation, original source authenticity or atomicity proof, randomization replay, Pine/live parity, return labels, economic inference, holdout, TV or deployment.

## Executed tests

Existing .venv, no dependency installation or upgrade.

```bash
.venv/bin/python -m pytest -q tests/test_hourly_impulse_classifier_support.py tests/test_hourly_impulse_classifier_audit.py tests/test_hourly_impulse_classifier_report.py tests/test_hourly_impulse_structure_event_support.py tests/test_hourly_impulse_structure_event_research.py tests/contracts/test_registries.py tests/boundaries
```

451 passed, 13 existing Pyparsing deprecation warnings, 26.46 seconds. This is one joint collection, not 451 newly added tests. The V29 files contain 29 core, 108 independent auditor, and 2 report-query synthetic tests. An earlier subset of 341 and core/auditor subset of 137 also passed; these overlap and must not be added together.

After final registry updates, the 16 registry contract tests passed again in 3.32 seconds. All six V29 artifact SHA256 and exact byte sizes were recalculated against their registered paths and matched. Unrelated concurrent registry entries were preserved, not included in this delivery's staged changes.

## Preserved report-layer failure

`build_report.py prepare` initially failed on SQLite `ambiguous column name: classifier_center`; full hour trace and request context both contain those names. `report_prepare_attempt1_failed.json` records the failed attempt; no report_data output was created and no upstream results changed.

Two synthetic SQL cases first reproduced the failure (2 failed), then passed after context qualifiers were added. Fixtures retain overlapping trace columns with deliberately conflicting values, both directions, short flat slope, strict band equality, warmup and missing-hour unknowns. Fix committed 92a6e57 before actual successful prepare. The SQL reads saved support tables only and reconciles all 251/744 rows and accepted counts before saving.

The first report-fix commit included a trailing blank-line whitespace warning in its new test file. The blank line is removed in the final scoped delivery; this did not change SQL or study semantics. Final staged whitespace check is required before delivery commit.

## Narrative and chart checks

- Report data queries execute real SQLite against verified counts, cases, controls and saved hourly trace. No hypothetical SQL or invented chart dataset.
- All four rejection combinations are mutually exclusive. Case rejection 67+33+70=170; classified state 168 neutral+2 opposite=170. Unknown3 stays in denominator. Controls have the same definitions at their own clocks.
- Neutral is an indicator state, not an empirically confirmed sideways regime or failed trade. Frozen 80 threshold is feasibility, not power or profitability inference. 78 does not establish efficacy or inefficacy.
- Final independent read-only report review found no blocking mismatch against summary/audit/report_data/plan; no outcomes or raw prices read by reviewer.
- Canonical artifact retains all 13 authored sections, 14 blocks with one native chronological 24-month count chart, no redundant legend, exact accepted counts, original denominators and unknowns in data. Source query returns both populations; chart explicitly filters population=case. No equity or profit plot.
- Required MD-to-HTML conversion executed immediately after authoring, then official portable delivery generated the canonical HTML at the same target.

## Portable delivery limitations

Official validation and package passed. Verification is **structural_only** because no compatible installed Chromium headless-shell executable was found. Semantic fallback remains packaged. Browser rendering, mobile overflow, source dialog and interaction are **not verified**; no browser installation or substitute automation was attempted. See `portable_delivery_receipt.json` for the exact result.

Opening the HTML in the Codex panel is delivery, not visual QA. Support audit pass, test pass, and HTML structure pass do not imply a trading-profit pass.
