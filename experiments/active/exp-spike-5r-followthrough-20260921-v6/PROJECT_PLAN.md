# V6: price paths, confirmation, market context, morphology process and two additions

Owner on 2026-09-21 explicitly requested trying all directions in the annotated
5R research plan. This authorizes isolated offline tests, not deployment, new
YOLO/ML fitting, live orders or changes to frozen production thresholds. Each
arm changes one independent research axis. Prior failures remain preserved.

## Frozen universe and responses

All 49,207 original V9 long requests from the authenticated V3 dataset, including
losses and censored paths; 3,531 source streams, 3,169 with requests. Do not
restrict to known winners or current top-performing symbols. Strict final net
R >5 primary, >10 secondary. Original stop formula, raw reverse exit,
2R-close/4ATR trailing and 20bp roundtrip remain unchanged for entry experiments.
Report candidate coverage, invalid/censored, tail precision and original-winner
retention/loss/gain, net bp, and matched random timing. Never sum independent
paths into a 100U account or imply liquidation safety from OHLC.

## Predeclared experiments

1. Recompute lower/upper bounds of fee-adjusted favorable excursion before
   ORIGINAL exit. Survived pre-exit bars contribute highs; opening reverse/gap
   exits contribute only their opening fill. A stopping candle's high is an
   ambiguous upper bound. Classify realized>5, confirmed touch-and-giveback,
   never observed>5, ambiguous exit-bar, and unresolved. This is diagnosis, not
   proof of fills at peak prices; no new exit optimizer is authorized here.
2. Wait1 and wait2, with original signal stop retained. Reprice initial risk
   using actual new next open. Any waiting completed stop breach, raw opposite
   signal, gap or invalid fill cancels entry. Compare confirm1 (next close >=
   original signal close and original six-MA ceiling), confirm2 (both waiting
   closes above original ceiling, last >= original close), and retest2 (first
   waiting low touches ceiling, its close holds above, second close exceeds
   first high and original close). Compare confirmation to corresponding
   waiting-only arm to isolate confirmation from mere elapsed time.
3. Separately gate on same-venue BTC trailing24h>0, BTC completed4h close>SMA20,
   symbol completed4h close>SMA20, symbol completed4h SMA20 slope3>0, symbol
   trailing24h return minus BTC return>0. No unfinished4h bar or stale fallback.
   BTC missing in a source cell stays unknown, not passed or cross-venue-filled.
4. Separately gate six-MA current span below prior96bar median; prior compact
   run above/equal the median of original candidates in previous3 completed UTC
   months (same timeframe, >=100 finite samples); last4 vs previous16 span
   expansion>1; prior32 known failed-upbreak count=0. Pure causal geometry;
   definition is a research hypothesis, not owner-confirmed perfect morphology.
5. Reuse existing structural profit-add engine with 0 and 2 adds, 100U per
   independent event, 1U gross initial risk, 1x cash-backed exposure ceiling.
   Structure0 vs original measures changed structural protection; structure2
   vs structure0 isolates adding. Do not attribute stop-policy differences to
   adds or claim these are maximum-leverage 100U results. Fees apply each leg.

## Time and controls

First year: entry AND resolved exit before 2025-09-10. Later: entry on/after that
date, before2026-09-10; crossing/unknown separately. No random temporal split.
No outcomes choose a new threshold. Dynamic cutoffs only use prior3 fully
closed calendar months of features. Diagnostic top-decile ranks use the same
past-only thresholds. Include old risk-low10% single-feature reference.

Random timing controls match same exact stream, actual decision UTC month,
anchor ATR/price bucket (.005/.01/.02/.05/.1) and earlier/later fold, excluding
all original V9 long signal anchors. Draw one deterministic control per event,
without inspecting outcomes or redrawing invalid/censored paths. Delayed
controls obey the same confirmation/lag/stop rules; structural controls use the
same add policy and costs. Feature filters reuse the original-entry matched
random control, so their comparison is against within-month random timing,
not an assertion of independence from all market beta.

Month-block paired null and rate intervals retain correlated market dates;
Holm correct the finite arm family. Report AUC and prior-threshold top-decile
gross/net returns of each causal descriptor, with risk baseline. No classifier
is trained. Existing history is already exposed: later evaluation is temporal
replication, not untouched confirmation. If an arm meets every frozen research
gate, perform serial position-state verification before claiming a strategy.

## Execution and integrity

Commit builders/tests/config/plan before generation. Freeze source manifest,
per-stream completion and cache hashes, same-venue BTC identity, dependencies,
counts and output hashes. Reuse completed identity-matching streams only.
Keep unknowns and failures. Root owns runner/report; Terra High owns only pure
context feature module and its focused tests, no nested delegation.

```bash
.venv/bin/python -m pytest -q tests/evaluation/test_spike_5r_followthrough.py tests/evaluation/test_spike_5r_context_features.py
.venv/bin/python -m yoyo.evaluation.spike_5r_followthrough --output experiments/active/exp-spike-5r-followthrough-20260921-v6/run_v1 --workers 4
```

No new dependencies, production thresholds, clocks, ACTIVE, models or Pine.
Register results, failure explanations and a learning; publish an observational
research record to the owner's Spike Notion hub, without a validation claim.
