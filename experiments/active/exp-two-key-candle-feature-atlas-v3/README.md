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
