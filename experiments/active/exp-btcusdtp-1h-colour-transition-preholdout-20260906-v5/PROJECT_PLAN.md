# V5 — entry-boundary colour and actual colour transitions

Owner: autonomous research toward post-cost profitability, not live trading.
Frozen before any new entry-colour/outcome join or replay. All code/config must
be committed before the driver opens prices. This is a finite single-variable
comparison, not a new parameter search or an assertion that the old exit is a bug.

## Question and design

V4 found 27/55 K2 executions ended after 5min. Saved trade ledgers alone could not
establish their pre-entry native5m colour. We first join the exact completed5m
bar ending at entry to each request, without entry-bar extrema or later data.
Colour is the owner-derived HL2>=SMA40 state, not bullish/bearish candle body.

Each identical entry is then replayed with two exits: existing opposite-state
exit, and actual adjacent same-direction to opposite-direction colour transition.
The primary population is all251 original direct K1 mothers. The complete K2
waiting strategy remains a diagnostic paired branch: all251 mothers and observed
non-entries remain, not only its55 eventual executions. No thresholds are chosen
from the diagnostic strata. One main hypothesis; the K2 replication is exploratory.

## Exact transition contract

- Only native5m SMA40, one completed opposite bar, no confirmation-length grid.
- Initialization uses the management bar opening entry-5min and ending exactly
  entry, checked against contiguous raw source timestamps/segments. It cannot
  exit at entry. If initially same-direction, first later completed opposite
  exits at the next execution open. If initially opposite/unknown, wait for a
  completed same-direction bar, then an adjacent completed opposite bar.
- Missing, stale, invalid/nonfinite colour breaks the transition chain. A later
  aligned observation re-arms it; no inference through missing bars. Raw price
  gaps remain censored, not zero returns. Do not compare independently numbered
  resampling segment IDs to raw segment IDs.
- Immutable K1 extreme protection always applies, including while unarmed.
  Gap-open stop precedes colour; then original mother+72h timeout, then intrabar
  stop-first. No extra grace period, stop widening, trailing or partial TP.
- On fully observed initially aligned paths, the new mode must exactly reproduce
  the old exit. Deviations require a timing/implementation investigation.

## Controls, denominators and inference

Reuse the pinned V4 assignment:462 control mothers for154/251 original mothers,
selected before waiting/outcomes with identical direction, month, UTC6h bucket,
causal ATR tercile, known5m/hour colour and signed hourly slope. No rematching,
coverage fallback or dropping unprofitable controls. Controls execute the same
state/transition mode and K1 stop-risk transfer. Invalid risk is observed non-entry
zero; gaps unknown. The inherited61.35% coverage cannot pass90% coverage gate;
this round may identify a mechanism, but cannot alone establish system success.

Report trade and mother-intention separately, net/gross bp, PF, win rate, all four
half-year folds, month support, cost+10bp stress, leave-top-two, serial single
pending/position replay, matched same-policy excess, range-ATR single-feature
AUC and top-decile baseline. Primary economics use unchanged20bp all-in assumption;
actual venue fees/funding/slippage remain unverified and no real profit promise.

Paired mother changes have 9999 month-cluster resamples and sign flips, seed20260906,
95% percentile interval, Holm across the two cohort contrasts. Report distribution
SD/median/IQR, lag1 month correlation and missingness; do not use IID trades or
normality-dependent t-tests. These intervals do not cure prior data reuse, cross-
month overlap or adaptive search. No power/independent confirmation claim.

Predeclared failure strata: known entry aligned/opposite/unknown; 5min and<=30min
exits; hard stops, colour exits, fee flips, MFE>=1R givebacks, nonpositive paths,
holding time/MAE. Timing outcomes/first arm time never become entry features.
Show all changed trades and lost former winners, not just improved examples.

## Acceptance and next steps

Only the direct primary arm can be nominated, and only after all unchanged gates
plus complete maternal evidence, positive monthly clustered matched excess at
p<.01, and positive paired effect at Holm p<.01. K2 remains diagnostic (55<80).
No audit entry point this round; a positive mechanism needs separately frozen,
properly matched verification. Do not consume 2025+ or repository holdout here.
On failure, use the observed clock evidence to prioritize a separate small-cycle
re-alignment entry hypothesis; never add that entry change to this comparison.

## Reproduction and source basis

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/test_hourly_impulse*.py tests/contracts/test_registries.py tests/boundaries/test_layer_imports.py
git branch --show-current
# Commit the explicitly owned builder/config/tests before the next command.
PYTHONPATH=. .venv/bin/python -m yoyo.evaluation.hourly_impulse_transition_research
```

Raw archive, prefix loading, folds and maternal embargo are inherited unchanged
from V1. The driver checks parent bytes and all V4 input hashes before loading.
Official timing references: [pandas2.3 backward known-at join](https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.merge_asof.html),
[TradingView completed bars](https://www.tradingview.com/pine-script-docs/concepts/bar-states/).
Source decisions and synthetic tests take precedence over screenshot impressions.
