# V36 verification record

Research outcome: rejected. Profitability goal remains active and unachieved.
This is same-source hourly morphology with a fixed native5 research exit, not
the deployed15m Pine strategy or a live execution certification.

## Causal and execution evidence

- Builders/config committed c7d17b7, corrected4e57139 before outcomes; no output
  or outcome labels from the first failed prefix merge. Corrections and unknown
  entry accounting documented in REVIEW.md, never zero-filled missing paths.
- Input24 V34 monthly files:210528 complete5m,17544 complete1h,2023-2024 only.
  Stored hashes checked before economic replay and again for clock diagnosis.
- Original63 candidates /44 K1 /108 controls /36 full three-control pairs;
  27 unmatched cases kept in the full baseline. Prefix8 candidate checks and16
  case/control flow checks passed. Pre-outcome identity receipt exists.
- All63 case and108 control flow windows complete; all171 paths closed; unknown
  price paths0. 23 flow-positive events; four-halfyear counts10/9/4/0.
- Clock diagnostic committed d8920c3 before rereading the same approved source.
  All63 exact clocks known. Five saved crosscount cells: later colour15, first
  colour without new flip38, first colour with new flip5, later stop2, first
  stop3. Thus the43=38+5 decomposition excludes hard-stop-bar future closes.
- Core reviewer independently reconciled summaries and proposed four reporting
  corrections; all locally verified. Matched improvement3.19522bp decomposes
  into gross−.50849 plus saved-cost3.70370, not evidence of predictive alpha.

## Tests

Focused final suite (projectvenv, Python3.9.6 / NumPy2.0.2 / pandas2.3.3):

```bash
.venv/bin/python -m pytest -q tests/test_owner_k1k2_genuine_flow.py tests/test_k1k2_owner_source.py tests/test_owner_k1k2_flow_report.py tests/test_owner_k1k2_exit_clock_audit.py tests/test_k1k2_genuine_flow_alignment.py tests/test_hourly_impulse_k2_matching.py tests/test_hourly_impulse_execution.py tests/test_hourly_impulse_data.py
```

297 passed. Report/clock subset rerun after reviewed packaging edits:34 passed
in0.51s. Synthetic tests cover source geometry, prefix stability, unchanged
completed bars, real-flow zero/unknown, retained original controls, fees,
invalid versus missing entry, exact colour clocks, and empty-fold SQL NULL.

Expanded command: the same eight paths plus `tests/boundaries tests/causality
tests/parity tests/contracts`. **852 passed,4 failed,14 warnings,in29.19s**.

1. Migration ledger candidate hash: expected8c2594ef… actual133a97a7…;
   this file already had unrelated edits before V36.
2. Migration ledger renderer hash: expected0962812f… actualdef5b7317…;
   actual matches already-committed HEAD, no V36 edit.
3. `test_known_conclusions.py::test_every_authorized_holdout_consumer_remains_explicit`:
   frozen consumer set differs from current registry.
4. `test_registries.py::test_holdout_consumption_is_declared_per_experiment_not_assumed`:
   V36's conservative incidental-inspection flag is an extra consumer.

The final two are NOT both dismissed as unrelated failures. V36 really recorded
unapproved incidental exposure, which must not be relabeled authorized to make
tests green. No allowlist, ledger, unrelated code or dependency was changed.
Earlier narrower projectvenv check732passed/2failed is superseded by the explicit
expanded command above. System Python additionally failed three non-numeric
dependency pins; no installs attempted. The numeric versions used for actual
economics and projectvenv diagnostic match; no full-repository-green claim.

## Report delivery

- MD converted immediately with scripts/md_to_html.py; canonical report built
  from the same frozen outputs. SQLite segment/failure/pair totals reconcile.
- Initial e5c925f artifact and b8616bd reviewed artifact retained as superseded
  snapshots. Final3f1ddd7 `artifact_reviewed2.json` has13 markdown sections plus
  one native grouped bar,8 rows,10 source entries. Source text states the zero
  trade group has no per-trade mean and states every outcome limitation.
- Canonical validate_artifact passed. deliver_portable_artifact validation and
  packaging passed, verification **structural_only**. Exact-payload and semantic
  fallback checks passed. No installed Chromium headless-shell was available;
  no browser download or substitute custom renderer. Desktop/mobile/theme/
  source-dialog behavior NOT verified. Details in delivery_receipt.json.

## Scope incidents and handoff

Two incidental out-of-scope text exposures: helper source search displayed one
old2026-09-03 anchor row; later root shared-registry diff displayed another
task's2026-09-01 ARB screenshot result metadata. Neither was authorized for this
configuration nor used for hypothesis/features/selection/scoring. No claim of
full-process holdout isolation. The actual V36 economic and clock inputs remain
2023-2024. Restrict subsequent status inspections to paths/counts and exact
current-task hunks. Scope disclosure learning notes retained.

NEXT_EXPERIMENT.md freezes a planned, NOT RUN, single exit-event comparison.
No new market scope, tuning, training, promotion, deployment, TradingView edit
or real-money operation. Negative V36 result and old transition-exit failures
remain citable; no active/frozen config was changed.
