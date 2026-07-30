# Todo 9b — Digest ↔ pipeline anomaly glue

**Result: PASS**  
**When:** 2026-07-10  
**Branch:** `codex/grok-2day`  
**Baseline:** Todo 9 `task-9-e2e-workflow.md` (digest dry-run printed “系统：无异常”, no anomaly ids)

## Hypothesis

If digest dry-run imports read-only pipeline `anomalies[]` and formats top ids,
operators see the same health flags as `/api/ops/pipeline` without Telegram,
writes, or holdout.

## Predeclared pass/fail

| Criterion | Result |
|-----------|--------|
| Pure format: empty → healthy, no alert | PASS |
| Pure format: injected warn/crit → ranked + alert | PASS |
| Info-only does not raise header alert | PASS |
| `main --dry-run` never calls `send` | PASS |
| Live dry-run lists real anomaly ids | PASS (2: fingerprint_mismatch, forward_low_sample) |
| Unit + pipeline regression | PASS 13 tests |
| Telegram / holdout / ACTIVE / E2.1b untouched | PASS |

## Commands

```bash
PYTHONPATH=. python3 -m pytest tests/test_daily_digest_anomalies.py tests/test_ops_pipeline_status.py -q
PYTHONPATH=. python3 scripts/daily_digest.py --dry-run
```

## Live dry-run footer

```
telegram_send: SKIPPED
alert_flag: True
anomaly_count: 2
anomaly_ids: fingerprint_mismatch,forward_low_sample
```

Body section (new):

```
管道健康：2 flag(s)（top 2）
- warn fingerprint_mismatch — ACTIVE dataset fingerprint mismatch...
- info forward_low_sample — Decision trades 7/100 below 25%...
```

## Comparison

| | Pre 9b | Post 9b |
|--|--------|---------|
| Digest pipeline flags | none (“系统：无异常” only) | top anomaly ids + count |
| Dry-run machine footer | alert_flag only | + anomaly_count, anomaly_ids |
| Tests | 7 pipeline | +6 digest glue = 13 focused |
| Alert header | data-freshness only | + warn/crit anomalies |

## Bottleneck / next

1. E2.1b still running (observe-only) — Todo 7 report waits exit.
2. Shadow H1/forward_track_shadows idempotency slice still optional.
3. Owner LS annotations still 0.

## Risk / honesty

- Loader calls `pipeline_status_payload()` (disk metadata scan); read-only.
- Warn/crit raise digest alert header; info alone does not.
- No Telegram token use; send remains non-dry-run only.
- Anomaly thresholds unchanged from Todo 9.
