# Multi-book shadow matrix idempotency

**Result:** PASS

**When:** 2026-07-10

**Branch:** `codex/grok-2day`

## Hypothesis

Running the complete declared shadow registry twice must not duplicate rows,
rewrite `models/ACTIVE`, or approximate unsupported challengers.

## Command

```bash
PYTHONPATH=. python3 scripts/forward_track_shadows.py \
  --books tp5_sl2_long_swap h1_scaled_25_t3 h8_30m_h48 h10_short_tp5_sl2
```

The command was run twice against the same cache.

## Results

| Book | Run 1 | Run 2 | Duplicate keys |
|---|---:|---:|---:|
| `tp5_sl2_long_swap` | 9 rows, 0 new, 0 updates | 9 rows, 0 new, 0 updates | 0 |
| `h1_scaled_25_t3` | 8 rows, 0 new, 0 updates | 8 rows, 0 new, 0 updates | 0 |
| `h8_30m_h48` | unsupported, not run | unsupported, not run | 0 |
| `h10_short_tp5_sl2` | unsupported, not run | unsupported, not run | 0 |

The before, after-run-1, and after-run-2 SHA-256 snapshots were identical:

- `models/ACTIVE`: `1d29b9dd17d21a2ee7849585f15f7899cbe4b4d56a063a68d2a41bd6a8aa9b2f`
- `data/forward_log.csv`: `d3cc3db558cf9413439d5c77261327f9a632d15ab5d3d3fa83cfbf4d6f932c63`
- `data/forward_log_h1_scaled.csv`: `9c66c2955334e084f1e40326062acd97f5d71333dcca8fd80815ab6043e1de76`

## Honest limits

- This is short prospective paper evidence, not a profitability claim.
- H8 remains blocked by the absence of a frozen 30m model.
- H10 remains blocked by the absence of a frozen short-side model.
- No holdout, promotion, threshold, cost, or live-order path was touched.
