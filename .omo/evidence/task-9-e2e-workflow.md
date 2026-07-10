# Todo 9 — workflow smoke + pipeline anomaly flags

**Result: PASS (anomaly code + local workflow smoke + VPS redeploy)**
**When:** 2026-07-10
**Branch:** `codex/grok-2day`
**Baseline:** Todo 6B `dc441fa` / `task-6-vps-pipeline.md`

## Hypothesis

Read-only `anomalies[]` derived from existing pipeline stage metadata (stale
data, fingerprint mismatch, low forward sample, YOLO evidence gap, executor on)
plus a safe local sequence (forward ×2, digest dry-run, pipeline API) gives an
operator health view without writes, Telegram, or holdout.

## Predeclared pass/fail

| Criterion | Result |
|-----------|--------|
| Unit tests healthy + injected failures | PASS 7/7 |
| Payload still redacted / read_only | PASS |
| Local live anomalies | PASS 2 flags: fingerprint_mismatch, forward_low_sample |
| Forward run 1 + 2 idempotent | PASS `new_signals=0`, `delta_rows=0` |
| Digest `--dry-run` no Telegram | PASS `telegram_send: SKIPPED` |
| VPS redeploy anomalies present | PASS anomaly_count=2; anon 401; executor 0 |
| LS still active | PASS |
| Shadow forward full suite | DEFERRED (mainline idempotency proven; shadows not re-run this atom) |

## Commands

```bash
PYTHONPATH=. python -m pytest tests/test_ops_pipeline_status.py -q
PYTHONPATH=. python scripts/forward_track.py   # twice
PYTHONPATH=. python scripts/daily_digest.py --dry-run
bash scripts/deploy_vps.sh
# auth GET /api/ops/pipeline → anomalies[]
```

## Snapshot

Local/VPS anomalies (identical shape):

- `warn fingerprint_mismatch` — ACTIVE dataset fingerprint vs frozen metadata
- `info forward_low_sample` — decision 7/100 below 25% target

Forward: total_rows=9, open=2, closed=7 both runs; no new keys.

## Comparison

| | Pre Todo 9 | Post |
|--|------------|------|
| Pipeline JSON | stages only | + `anomalies`, `anomaly_count` |
| UI 流水线 | stages | + health flag block + badge count |
| Tests | 5 | 7 (collect_anomalies healthy/injected) |

## Bottleneck / next

1. Digest dry-run still prints “系统：无异常” — does not yet consume pipeline
   anomaly flags (separate optional glue).
2. Plan Todo 7 (E2.1b report) remains observe-only until training exits.
3. Owner LS annotations still 0 — parallel gate.
4. Full shadow forward matrix not re-run this atom.

## Risk / honesty

- Anomaly thresholds (`DATA_STALE_HOURS=36`, low-sample 25%) are diagnostic
  only, not trading parameters.
- Fingerprint mismatch is reported honestly; no auto-promote/repair.
- No holdout, no E2.1b stop/start, no live orders, no Telegram token use.
