# Todo 9c / Option C — H1 shadow forward idempotency

**Result: PASS**  
**When:** 2026-07-10  
**Branch:** `codex/grok-2day` @ `1856936`  
**Baseline:** mainline forward ×2 already idempotent (Todo 9); H1 log had 8 data rows

## Hypothesis

Re-running `scripts/forward_track_h1_shadow.py` twice against the same market
cache does not duplicate shadow rows and never rewrites ACTIVE or mainline
`data/forward_log.csv`.

## Predeclared pass/fail

| Criterion | Result |
|-----------|--------|
| Run1 `new_signals=0` (or stable total_rows) | PASS new_signals=0, total_rows=8 |
| Run2 identical | PASS new_signals=0, total_rows=8, same file SHA |
| No duplicate (symbol, signal_time) keys | PASS dup_keys=0 |
| Mainline log SHA unchanged | PASS |
| ACTIVE unchanged | PASS `models/frozen_tp5_sl2_swap_20260709.txt` |
| No promote / holdout | PASS |

## Commands

```bash
PYTHONPATH=. python3 scripts/forward_track_h1_shadow.py   # ×2
```

## Snapshot

| Metric | Run1 | Run2 |
|--------|------|------|
| scanned_series | 358 | 358 |
| candidates_seen | 31766 | 31766 |
| threshold_signals_seen | 8 | 8 |
| new_signals | 0 | 0 |
| closed_updates | 0 | 0 |
| total_rows / open / closed | 8 / 1 / 7 | 8 / 1 / 7 |
| H1 log lines (incl header) | 9 | 9 |
| H1 file SHA | 9e03085… | 9e03085… (identical) |
| mainline SHA | 3eff47e… | 3eff47e… (identical) |

Duration ~3 min total (two full scans).

## Comparison

| | Mainline (Todo 9) | H1 shadow (this) |
|--|-------------------|------------------|
| Log | forward_log.csv | forward_log_h1_scaled.csv |
| Idempotent re-run | yes | yes |
| Touches ACTIVE | no | no |

## Bottleneck / next

1. Full multi-book `forward_track_shadows` matrix still optional (unsupported books reported only).
2. E2.1b still training — Todo 7 formal report waits exit.
3. Fingerprint mismatch still open diagnostic (not this atom).

## Risk / honesty

- Shadow only; not promotion evidence.
- Scaled artifact remains stub; entries scored with mainline freeze (documented in payload).
- `data/` not committed (gitignore).
