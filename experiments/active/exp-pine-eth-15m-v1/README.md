# ETH perpetual 15m Pine research v1

This directory freezes the first ETH-only 15-minute research contract derived
from the user-supplied ALLIN-V7.2 script.  It uses the bounded OKX
`ETH-USDT-SWAP` 15m series as the local research proxy and stops at
`2026-03-01T00:00:00Z`, 64 days before the repository holdout.

The supplied V7.2 attachment is hash-verified in `migration_audit.json`.
Sixteen static migration checks record the execution fixes: explicit cost and
slippage, close-only calculation, risk sizing instead of fixed 4x/time boosts,
fill-anchored stops, one reversal path, explicit Hong Kong time, and fail-closed
15m/ETH/date/percentile guards.  The audit also keeps the unresolved
cost-underwater break-even and missing TradingView parity visible.

The selected V9 candidate keeps the V7 SMA10/SMA60 crossover, EMA100 regime,
ATR14 stop and oscillator construction.  It makes two alpha changes selected
in temporal development blocks: the existing project feature
`slow_slope_12` must agree with direction, and the oscillator threshold is
`0.1` instead of `0.2`.  Position risk defaults to 1% to reduce drawdown; that
is a sizing overlay, not evidence of stronger unit alpha.

After V9's final-preholdout period had already been inspected, a second
project feature (`vol_ratio_mean8 >= 1`) improved the historical point estimate
and drawdown.  That V10 result is recorded only as the next forward hypothesis:
it is post-selection, more tail-concentrated, and not independent OOS evidence.
V9 remains the last candidate whose two alpha changes were locked before its
single final-preholdout evaluation.

A development-only nested ablation shows where that improvement comes from.
SMA10/60 cross-only and cross-plus-EMA100 were negative after cost in every
2023/2024 half-year.  EMA200 slope12 created the first positive weighted
expectancy, oscillator direction reduced noise, and the 0.1 threshold was the
first stage positive in all four halves.  This is evidence for a sparse
trend-aligned crossover, not evidence that a strict moving-average-density
shape has been learned.

That semantic distinction is now measured directly.  Across the 276 V9
executions in 2023 through February 2026, only 4 (1.45%) satisfy the project's
unchanged strict EMA8/13/21/34/55 plus EMA144/200 density mask, and only 29
(10.50%) satisfy the expanded mask.  The final period has 1/110 strict
overlaps.  Exact within-split circular time-shift nulls find no strict-density
enrichment in any split.  Pine V9 and Local Signal V2 therefore share a
research motivation, not candidate semantics; the old judgment model cannot
be reused as if their rows were interchangeable.

The volume gate was selected again in all three incremental prequential
feature replays, but the exact three-block sign-flip p-value is 0.125 and the
18-gate selection-adjusted max-stat p-value is 0.50.  It remains a useful
paper-forward hypothesis, not a proven optimization.

A direction-eligibility ablation independently selected long-only in
development.  Its consumed-final diagnostic is +63.39 bp/trade with 16.77%
drawdown, but only 5/56 trades win, leave-top-one-out expectancy is negative,
and its week sign-flip p-value is 0.27.  It is registered as V11 paper A/B,
not as a replacement for frozen V9.  V10 and V11 must remain separate
single-variable arms; combining them would be a new unapproved bundle.

Matched-control assignment uncertainty is also explicit.  Across 64 exact,
non-reused assignment seeds, V9 and V10 have positive excess in 89.06% of
seeds and V11 in 95.31%, but none of the 192 seed/variant runs has week-block
p<0.01.  A favorable control seed may not be cherry-picked.

Capital-path risk is reported separately from alpha.  In a 20,000-run circular
four-week block bootstrap, V9 at 0.5% risk has a 95th-percentile maximum
drawdown of 18.44%, versus 33.66% at 1% risk.  The 0.5% arm still ends negative
in 22.60% of resamples, so sizing reduces damage but does not validate edge.
The canonical signal comparison remains at 1%; 0.5% is the conservative paper
risk profile.

The actual-timeframe audit uses 20,328 gapless OKX 5m rows to build 10,164
exact 10m parents over the only available common window.  From 2025-12-23
through 2026-02, original V8 is +27.39 bp/trade on 10m but -39.78 bp/trade on
15m; V9 is -15.99 bp/trade on 10m and -58.68 bp/trade on 15m.  None passes its
matched-control test.  This short consumed-final diagnostic cannot overturn
the longer V9 freeze, but it disproves any claim that 15m is inherently better
than the original 10m surface.

A reverse-time 2022 backcast is favorable to V9 (+108.66 bp/trade, +81.69 bp
versus exact controls), while V10/V11 are weaker.  Its week sign-flip p=0.0624
and parameters were selected later, so it is explicitly not OOS.  Across nine
fixed chronological blocks, V9 is positive in 7/9, but exact equal-block p is
0.0176 for absolute net and 0.0215 for matched excess; 2025H1 and 2026M1M2 are
negative.  Regime dependence remains material.

The frozen exit anatomy exposes a semantic defect without changing barriers.
49/110 final trades reached the configured break-even stop, but the +10 bp
lock is below the frozen 20 bp round-trip cost, so every one exits at exactly
-10 bp project net.  Another 50 trades hit the initial protective stop, and
11 reverse exits generate all positive trades.  A static same-exit accounting
illustration shows that making those 49 exits exactly zero net would add only
4.45 bp/trade and still would not remove top-winner dependence.  Any actual
break-even parameter change still requires owner approval and a new replay.

Nearby-feed sensitivity supports the price-only baseline: V9 executed-entry
Jaccard is 96.61% between OKX swap and spot over the common window, versus 78%
for volume-gated V10.  Spot is not treated as a perpetual substitute or
TradingView parity.

The paper hypotheses have mechanically generated Pine surfaces alongside V9:
`allin_eth_15m_v10_volume_paper.pine` and
`allin_eth_15m_v11_long_only_paper.pine`.  Their manifest hashes the V9 source
and both outputs, and explicitly refuses a combined V10+V11 arm.  Neither file
has TradingView export parity.

The judgment-layer bridge is prepared without training.  A 166-row 2023/2024
lineage table contains all 28 causal side-aligned features, conservative label
ends, and three expanding time folds with label-overlap purge.  Every row and
the manifest remain `training_eligible=false`; no LR, LightGBM, scaler, score,
or threshold was fitted.  Because rejecting an entry changes later position
and cooldown state, any future authorized model must be evaluated inside the
dynamic replay rather than by filtering the existing trade CSV.

The complete feature-only gate surface contains 335 guarded V9 raw candidates,
so the 166 executed baseline rows cover only 49.55%; 169 signals can become
relevant after prior gate decisions change state.  `judgment/` therefore
defines a fail-closed score template: exact raw-candidate coverage, score ready
by next-open, fixed model/feature hashes, preregistered threshold, and dynamic
replay.  No score or threshold currently exists.

The executable bridge now self-audits this contract.  A synthetic allow-all
sentinel reproduces the 83-trade 2023 and 83-trade 2024 V9 ledgers exactly
(maximum numeric error below `5e-13`), while eight deliberate missing,
duplicate, timing, value, hash and preregistration mutations all fail closed.
It also preserves a non-obvious Pine ordering rule: ineligible raw signals
still consume cooldown before the calendar/volatility gate.  No model was
trained or loaded and no threshold was selected by this identity test.

The capacity audit also blocks a premature full model: those 166 rows contain
only 27 net-positive events for 28 features (0.96 events/feature), and the
walk-forward validation folds contain only 4–8 positives each.  A future
authorized first model should therefore be a preregistered one-feature
regularized LR (or at most a tiny prior-chosen subset), not 28-feature
LightGBM.  Static ledger filtering is empirically biased: for the volume gate
it gives +50.50 bp/trade versus +41.22 bp in dynamic replay and only 84.52%
entry Jaccard.

A no-training judgment-signal audit makes the limitation concrete.  Four
transparent one-feature priors were scored in expanding folds and compared
with all 68,400 combinations of within-half-year circular outcome shifts.
`vol_ratio_mean8` is the strongest static diagnostic (+365.67 bp in 14
top-decile rows), but only 3 rows win and its raw/Holm top-decile p-values are
0.0595/0.2380; the Holm family does not cover its earlier 28-feature selection
history.  A flexible 28-feature prequential selector is worse: pooled next-fold
AUC 0.430 and 0/13 positive top-decile rows.  No LR or LightGBM was fitted.

The durable search ledger now has an explicit selection-budget audit: 12
oscillator thresholds, 11 slope lags, 18 natural gates, 3 side policies and 21
trailing configurations are 65 known configurations / 60 unique four-block
performance paths.  A common-block exact max-stat chooses long-only but gives
p=0.25.  This ledger cannot recover every code iteration or human choice, so
it is a lower bound on selection pressure.  More 2023/2024 mining is stopped;
V10/V11 require fresh forward evidence.

The three Pine files are now hashed into a blocked paper-forward protocol.
No collection, log, paper order, or live order was started.  Historical arrival
rates imply roughly 12.7, 18.1, and 24.9 months for V9/V10/V11 respectively to
reach 100 fresh trades.  A TradingView normalized-export template and
fail-closed 110-trade reconciler are ready under `tradingview/`.

Important limitations:

- `ETHUSDT.P` is a TradingView display convention, not a venue identity.  The
  OKX cache is not assumed to be bar-identical to Binance, Bybit, or another
  TradingView perpetual feed.
- The 2025-01 through 2026-02 final-preholdout period has now been inspected.
  It is no longer an unseen OOS set for this strategy family.
- TradingView's official Pine v6 compiler accepted the exact V9 hash on
  `OKX:ETHUSDT.P` 15m with zero compile errors.  Trade export parity has not
  passed: the Basic plan's loaded range began after `researchEnd`, and arbitrary
  historical Deep Backtesting required an upgrade.  Python results therefore
  remain non-deployable broker-emulator diagnostics.
- Ordered 3-minute replay on the same OKX feed reconstructs all 40,704 final
  15-minute bars exactly and reconciles all 110 V9 exits and prices.  That
  rejects hidden 15-minute aggregation optimism locally, but still is not
  TradingView venue parity.
- Matched controls use unique entry starts and split-contained exits.  Their
  return windows may overlap because multi-week trend holds otherwise make an
  exact same-regime control impossible; inference is therefore clustered by
  UTC week rather than pretending trades are independent.
- The existing project LightGBM model is not a valid Pine gate.  The exported
  feature table is explicitly training-ineligible while P0/P1 blocks training.
- Nothing here changes `models/ACTIVE`, creates `active_bundle.json`, promotes,
  deploys, writes forward logs, or touches a live account.
- An exploratory shell `tail` displayed two raw post-holdout 3-minute rows
  before the bounded loader was written.  They were never loaded, scored, or
  used in a strategy calculation.  The incident is retained in the report
  because this repository records any holdout look, including an accidental
  operational preview.
- Local ETH funding history starts only on 2026-04-07 and does not overlap the
  canonical backtest, so funding-adjusted returns are unavailable rather than
  silently treated as zero.  A later shell coverage check accidentally printed
  eight holdout-period funding rows; they were not loaded, aggregated, scored,
  or used, and the funding audit stopped immediately.
- The TradingView compiler attempt opened the site's default 2026-06 to
  2026-08 chart before the safe-window limitation was known.  V9's date gate
  entered/scored zero trades and no metric or parameter decision used the
  visible post-holdout prices.  The incident is recorded rather than silently
  converting a compiler smoke into a clean holdout claim.

## Reproduce

```bash
PYTHONPATH=. .venv/bin/python scripts/research_pine_eth_15m.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_robustness.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_side_hypothesis.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_control_sensitivity.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_path_risk.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_feed_sensitivity.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_exit_anatomy.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_backcast.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_actual_10m_vs_15m.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_regime_stability.py
PYTHONPATH=. .venv/bin/python scripts/generate_pine_eth_15m_paper_variants.py
PYTHONPATH=. .venv/bin/python scripts/prepare_pine_eth_15m_judgment_research.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_judgment_feasibility.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_judgment_signal.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_selection_risk.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_stateful_gate.py
PYTHONPATH=. .venv/bin/python scripts/replay_pine_eth_15m_judgment_gate.py --self-audit
PYTHONPATH=. .venv/bin/python scripts/audit_pine_eth_15m_static_contract.py
PYTHONPATH=. .venv/bin/python scripts/design_pine_eth_15m_paper_protocol.py
PYTHONPATH=. python3 scripts/reconcile_pine_eth_15m_backtesting.py
PYTHONPATH=. python3 scripts/reconcile_pine_eth_15m_intrabar.py
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_pine_allin_v7_backtest.py \
  tests/test_research_pine_eth_15m.py \
  tests/test_reconcile_pine_eth_15m_intrabar.py \
  tests/test_analyze_pine_eth_15m_robustness.py \
  tests/test_replay_pine_eth_15m_judgment_gate.py \
  tests/test_smoke_pine_eth_15m_artifacts.py
PYTHONPATH=. /tmp/fable-pine-eval-venv/bin/python scripts/build_pine_eth_15m_report.py
PYTHONPATH=. .venv/bin/python scripts/md_to_html.py \
  analysis/p0_pine_eth_15m_v1_20260821.md --out-dir analysis/html
```

The Docker recipe is an independent, read-only rerun surface:

```bash
docker build -t fable-pine-eth15m-v1 \
  experiments/active/exp-pine-eth-15m-v1/docker
docker run --rm --network none -v "$PWD:/workspace:ro" \
  -v "$PWD/experiments/active/exp-pine-eth-15m-v1/results-docker:/output" \
  fable-pine-eth15m-v1
```

On 2026-08-21, three builds stopped at the Docker Hub metadata request for
`python:3.11-slim`; the third was canceled after another 90-second metadata
wait. None reached dependency installation or project code.
An offline audit was therefore also run in a pre-existing local image with
`--network none`.  First, the artifact arithmetic smoke passed under Python
3.13 / pandas 2.2 / NumPy 2.2.  Second, the container read the bounded raw 15m
prefix, rebuilt the frozen V9 signals, reran the stateful execution engine and
matched all 110 canonical trades (direction, indices, exit reason and times)
with numeric error below `1e-10`.  Reproduce the full replay with:

```bash
docker run --rm --network none --entrypoint python -e PYTHONPATH=/workspace \
  -v "$PWD:/workspace:ro" \
  -v "$PWD/experiments/active/exp-pine-eth-15m-v1/results:/output" \
  heartexlabs/label-studio:latest \
  /workspace/scripts/replay_pine_eth_15m_offline.py \
  --config /workspace/experiments/active/exp-pine-eth-15m-v1/config.json \
  --canonical-trades /workspace/experiments/active/exp-pine-eth-15m-v1/results/trades.csv \
  --output /output/docker_offline_replay.json
```

This proves cross-runtime replay portability, not TradingView parity.  The
pre-existing image is not the pinned experiment image, so
`pinned_docker_recipe_built=false` remains recorded.

The report builder writes the canonical Markdown analysis.  Project policy
then converts it to HTML for owner delivery.
