# Two-key-candle causal feature atlas V3

This diagnostic starts from the frozen V2 `single_sma40_core_2_8` event set.
It does not rebuild labels, train a model, alter execution parameters, consume
the repository holdout, or promote anything. Its only purpose is to test the
owner-requested visual dimensions one at a time under a fixed chronology.

The analysis is frozen before its first run:

- discovery: 2023-01-01 through 2024-12-31;
- validation: 2025-01-01 through 2025-12-31;
- bridge: 2026-01-01 through 2026-02-28;
- descriptive fresh pre-holdout: 2026-03-01 through 2026-05-03;
- forbidden holdout: every bar at or after 2026-05-04.

Each feature family is evaluated independently. Within a family, the discovery
period selects one fixed bin by the preregistered robust score. The selected bin
is then replayed unchanged in validation, bridge and descriptive fresh data.
No interaction search is allowed in this experiment: it would violate the
single-variable interpretation and make the already-open fresh window a tuning
set. The matched control already attached by V2 is retained for every event.

The result is a feature atlas and a falsification audit, not a production rule.
Any apparently positive bin must still satisfy absolute post-cost profitability,
matched-control excess, time stability, uncertainty and multiplicity checks.

After the first 42-family run, a completeness audit found 13 material causal
columns that already existed in the frozen V2 table but were absent from the
atlas (including stop distance, MA ordering, oscillator level/delta and native
colour continuity). They were declared in `config.json` and committed before
their results were inspected. Because the first run had already opened 2025,
these additional families are diagnostic rather than confirmatory.

## Reproduce

```bash
PYTHONPATH=. .venv/bin/python scripts/analyze_two_key_candle_feature_atlas_v3.py
PYTHONPATH=. .venv/bin/python scripts/validate_two_key_candle_feature_atlas_v3.py
```

## Pine indicator

The owner-anchor morphology is also available as a Pine Script v6 overlay:

    pine/fable_two_key_candle_sma40_retest_v1.pine

It is deliberately an indicator, not a strategy. The broad profile marks K2
only after the bar closes and emits the causal entry on the next bar's first
update, when both the next open and exact K2-extreme risk are known. It also
offers the strict owner-morphology and owner-state profiles for visual
inspection, but labels the similarity score as non-predictive because V2/V3
found no stable post-cost edge.

Static, official-compiler and two-anchor parity:

    PYTHONPATH=. .venv/bin/python scripts/validate_two_key_candle_pine_indicator.py
    PYTHONPATH=. .venv/bin/pytest -q tests/test_two_key_candle_pine_indicator.py

results/pine_compile_receipt.json binds the official TradingView Pine v6
compile result to the exact source SHA. The source compiled with zero errors;
adding it to the owner's current chart was blocked only because that Basic
layout had already reached its indicator-count limit. The reference-style
source was saved privately as version 2; it was not published and no existing
indicator was removed from the layout. Its default view mirrors the owner's MA
Shift reference with teal/orange candles, a thin SMA40 and compact K1/K2 marks;
the six-MA rope, HUD and position boxes remain opt-in diagnostics.
