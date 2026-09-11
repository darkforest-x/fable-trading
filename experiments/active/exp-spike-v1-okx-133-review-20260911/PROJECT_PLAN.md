# SPIKE Burst V1 OKX 133-record visual review

Build a display-only chart corpus for the fixed Beijing-time window
`[2026-08-27 00:00, 2026-09-11 00:00)`: 127 long V1 covered-ledger rows plus
the six separately enumerated live-journal rows.  It does not train, tune,
replay outcomes, alter monitor state, or make a trading decision.

The builder verifies the immutable ledger filename/hash, selects only OKX
30m/1H/4H long records, detects identity duplicates, and requires exactly
75/42/16 records.  Covered rows use their frozen normalized OHLC source;
live rows copy their complete monitor checkpoint once into this experiment
before calculation.  Features are made from a full continuous segment before
the bounded display crop.  Future bars are visibly review context only.

Run only after the builder has been committed on `main`:

```bash
python3 -m yoyo.evaluation.spike_v1_review_data
```

The generated JSON lives under `data/` and is intentionally not a git
artifact.  The static viewer owns any later site-data copy.
