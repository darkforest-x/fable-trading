# V35 QA and honest delivery status

## Evidence chain

- 372ed6a: exact aligner/runner/config/plan plus synthetic tests committed BEFORE
  first real read. summary generated2026-09-07 08:35:11UTC names that exact commit.
- 14174d4: report builder with original-control parent-ID whitelist committed
  BEFORE report-only identity projection and SQL recomputation.
- 2484b83: narrative, SQL smoke, denominator learning and HL2-side label correction
  committed BEFORE canonical artifact packaging. Existing summary/report_data
  remain unchanged. Full prior V4/V34 inputs were not edited.

## Actual checks

```bash
.venv/bin/python -m pytest -q tests/test_k1k2_genuine_flow_alignment.py tests/test_k1k2_genuine_flow_audit.py tests/test_k1k2_genuine_flow_report.py tests/test_binance_flow_coverage.py tests/test_binance_um_flow_archives.py tests/test_binance_um_archives.py tests/test_binance_flow_unit_audit.py tests/test_binance_flow_coverage_report.py tests/contracts/test_registries.py tests/boundaries
```

419 passed in26.97s;13 pre-existing matplotlib/pyparsing deprecation warnings.
New V35 tests135 =116 pure aligner +18 roster +1 SQL smoke. Hypothesis absent;
existing pytest and deterministic seeded property families used, no dependency
installation or claim of a Hypothesis run. Twelve seed×availability families each
compare24 windows to an independent slow slot oracle, plus pinned counterexamples.

Initial129 combined tests passed before the added resolution tests; these exposed
three s/ms/us failures. Fixed before372ed6a and before any real input read; final
134 aligner+roster passed in1.03s. No real input failure or evidence overwrite.

All1001 windows complete;8 quarterly prefix replays exact. Original source/derived
hash checks before/after materialization and all9 saved file SHA rechecks pass.
No OHLC/returns read by new runners. Original6rosters may contain price columns,
but explicit usecols omit them; statuses are after-K1 audit fields, never K1
features. Parent projection adds identity/direction/fold only, with the same
frozen original file SHA. REVIEW.md contains independent denominator checks.

## Report and notebook

MD converted immediately via scripts/md_to_html.py, then canonical artifact
validated with Data Analytics validate_artifact and packaged with its portable
builder. Result13 blocks,12 separate markdown sections,1 native4-category chart,
1 bounded dataset,7sources. Chart labels are direct, count axis starts atzero;
single blue-root measure with no redundant legend. Native reader/semantic data
fallback share canonical input. Exact lookup tables are markdown by design.

delivery_receipt.json records structural_only: no compatible installed Chromium,
so no desktop/mobile, light/dark, chartSVG or source-dialog UI verification.
No browser installed. Do not claim the queued Codex file opened visually.

verification.ipynb has3code cells executed top-to-bottom in a fresh stdlib
namespace with captured outputs; source/result hash and denominator assertions
passed. nbformat/nbclient/jupyter/ipykernel are absent in the contract venv, so
this is NOT a Jupyter kernel/UI run. To run in an existing notebook-enabled
environment from repo root (without altering the contract venv):

```bash
python -m jupyter nbconvert --execute --to notebook --inplace experiments/active/exp-btcusdtp-k1k2-genuine-flow-alignment-20260907-v35/verification.ipynb
```

No economics or held-out acceptance performed. No strategy thresholds, costs,
barriers, trading configuration, ACTIVE, Pine/TradingView, orders, or training
changed. Goal remains active; alignment acceptance does not complete profitability.
