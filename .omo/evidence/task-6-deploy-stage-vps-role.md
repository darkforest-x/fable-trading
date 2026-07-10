# Todo 6 follow-up — accurate VPS deploy stage + durable ops.env wire

**Result: PASS**
**When:** 2026-07-10
**Branch:** `codex/grok-2day`

## Hypothesis

Reporting deploy `role=vps` when `PROJECT_ROOT` is `/opt/fable-trading`, and
re-asserting `EnvironmentFile=-/etc/fable-trading/ops.env` in `deploy_vps.sh`,
makes redeploys keep fail-closed auth and stops the misleading `local_ops` label
on the public host.

## Pass/fail

| Criterion | Result |
|-----------|--------|
| Local tests | PASS 5/5 `tests/test_ops_pipeline_status.py` |
| `bash -n scripts/deploy_vps.sh` | PASS |
| Redeploy shows ops_env:present mode=600 | PASS |
| VPS pipeline deploy stage | PASS `vps_executor_off` / `role=vps` |
| No `/opt/` path in stage JSON | PASS |
| Anon still 401; executor 0 | PASS |
| LS still active | PASS |

## Comparison

| | Pre | Post |
|--|-----|------|
| Deploy stage on VPS | `local_ops` | `vps_executor_off` |
| deploy_vps.sh | executor force only | + EnvironmentFile re-assert, ops_env presence note |

## Bottleneck / next

Todo 7: pipeline health anomaly indicators (stale data, fingerprint mismatch,
low forward sample, executor state) on the existing redacted surface.
