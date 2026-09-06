# V3 prior context, development only

## Decision and lineage

The V1 colour-exit candidate and all V2 partial-profit alternatives lost after
the frozen 20bp cost. V3 tests entry information, not another exit grid. This
protocol is committed before the new context outcomes. The same historical
development has already been searched; ordinary p-values are exploratory.

Four arms share complete prior-context availability. The original morphology
and hourly-slope arms replay historical anchors. The new contrast replaces
hourly slope with both 4h colour and slope agreeing with direction. Its nested
contrast adds only extension <= 1.5 ATR. That number is reused from V1, not
newly discovered or optimised. No bundled exit/MA/direction/cost changes.

## Availability, not hindsight

4h uses 48 complete UTC-aligned 5m bars. Its SMA40(HL2), colour and three-bar
slope / ATR must be available by K1 **open**, not close. All windows reset at
gaps. A known old context cannot cross a raw-data gap to a new K1. Common
support is applied to every arm and its removal count is reported. Entry still
occurs at the next 5m open after K1 closes; the K1 actual extreme stays the stop.

Reusing 1h SMA160 or the still-forming 4h would answer a different question.
Adding 4h on top of hourly slope would confound substitution with cumulative
strictness. These alternatives are deliberately not run in this finite test.

## Controls and inference

Assign three controls per event before simulating any outcome. Same month,
UTC 6h period, causal ATR tercile, known 5m colour and direction; additionally
match prior4h side and slope sign. Every control passes the exact arm's slope,
context and extension requirements. Transfer initial risk/ATR to its own
ATR and own entry open; use the identical 5m native40 exit and 20bp cost.
Reject insufficient exact matches, do not widen bins. No time reuse per arm.
Exclude crossings only when already known at the candidate decision boundary.

Report both retained and removed original signals, per-original-signal payoff,
folds, directions, months, gross/net, PF, single-position ledgers, matched
excess, censoring, leave-top-two and extra10bp. Monthly sign flips and a Holm
adjustment across the two new-arm tests are descriptive, not correction for
all prior research. Range-feature AUC/top-decile remains a single-feature
baseline, not a trained-model result. No new model is trained.

## Gates before ranking

All arms receive every gate in config; only passing new arms may be ranked.
At least80 events,12 per halfyear,4 positive halfyears,PF>1.1,12 active months,
3 months per fold,matched coverage>=90%,positive matched excess,positive
single-position mean,+10bp stress positive and leave-top-two positive.
Failure or insufficient evidence is preserved. No audit command exists, even
if a development arm passes. A future verification design would need to name
its reused/fresh provenance honestly rather than recycling V1's audit label.

## Reproduction

After committing builder/config, run:

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/test_hourly_impulse_context.py tests/test_hourly_impulse_context_matching.py tests/test_hourly_impulse_context_research.py
PYTHONPATH=. .venv/bin/python -m yoyo.evaluation.hourly_impulse_context_research
```

No Pine, TradingView, ACTIVE/frozen, VPS, forward log or trading account change.
