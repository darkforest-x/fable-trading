# V6 delivery verification

2026-09-06. Outcome: economic rejection; goal remains active.

- Independent saved-ledger checks: `VERIFICATION.md`. No second raw-price
  replay or external market feed was used for this review.
- Tests: `PYTHONPATH=. .venv/bin/pytest -q tests/test_hourly_impulse*.py
  tests/contracts/test_registries.py tests/boundaries/test_layer_imports.py`:
  **589 passed in9.72s**. An intermediate run had586pass/3fail while the experiment
  registry already referenced the two not-yet-added artifact rows. Completing
  those rows fixed the dangling references; no test or strategy was weakened.
- Main MD converted immediately with repository `scripts/md_to_html.py`, then
  replaced by the canonical native portable report, not delivered as a reduced
  Markdown conversion page. Source maps and queries live in committed artifact.
- Standard Data Analytics0.2.10 `deliver_portable_artifact.mjs`: validationPASS,
  packagePASS, verification`structural_only`;30blocks/two native charts.
  Browser warning`browser_unavailable`: no compatible Chromium headless-shell.
  Source dialogs/interactions not verified; no mobile or desktop viewport QA.
  No browser/dependency download and no custom rendering fallback.
- Compared to V5 commit4094a1a, original exit chart and its eight dataset rows
  are byte-for-byte/equality preserved;17prior markdown sections unchanged.
  Six dependent sections changed: title, summary, scope, next hypothesis,
  caveats, reproducibility. Five V6 narrative sections and one chart added.
- Distribution keeps all251 maternal observations: bucket counts
  `[3,4,8,2,218,4,6,5,1,0]`. Unequal intervals are explicit categories/counts,
  not density; unchanged and unknown buckets are retained. No outlier removal.
- Report identifiers: main MD SHA256
  `20daecec9215a2cd30f77b3842cf1fc95163aa6ed2c9c56050ce6967e45feed8`;
  native artifact`474983cabada037a52e328ae72465dac4eb49e43d9e9369369e0a01a2260aa9c`;
  HTML`a9dae83a5617a589efa73dab3c5ee87004365ac1993993de9bfae1d59dc304f9`.
- No TradingView, production config, ACTIVE, forward log, model training,
  deployment, real order or position changes. New entry hypothesis remains
  a research proposal, not an implemented or profitable next version.
