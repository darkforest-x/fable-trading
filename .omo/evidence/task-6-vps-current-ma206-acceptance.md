# Todo 6 — VPS current-MA206 final acceptance

**Result:** PASS

**Date:** 2026-07-10

**Public dashboard:** `http://103.214.174.58:8642/`

## Acceptance matrix

| Criterion | Live result |
|---|---|
| Coarse redacted public status | PASS `GET /api/pipeline` → 200 without token |
| Authenticated controls | PASS `/api/ops/pipeline` anonymous 401, authenticated 200 |
| Write controls disabled | PASS authenticated `POST /api/ops/jobs` → 403 |
| Every pipeline stage | PASS 7/7: data, YOLO, judgment, backtest, forward, jobs, deploy |
| Current MA206 mainline | PASS `ACTIVE=frozen_tp5_sl2_swap_ma206_20260710`, fingerprint ok |
| Forward caveat | PASS 0/100 shown with explicit non-promotion caveat |
| YOLO gate | PASS report and coarse metric keys shown; explicitly diagnostic only |
| VPS executor | PASS systemd and payload both `ENABLE_JOB_EXECUTOR=0` |
| No local path / secret leak | PASS JSON scan: no absolute host path or secret-value field |
| Desktop browser | PASS 1440x1000, root overflow 0, console 0/0 |
| Mobile browser | PASS 390x844, root overflow 0, no token entered, console 0/0 |
| Label Studio coexistence | PASS service active; nginx public 8081, backend loopback 8082 |

## Public payload snapshot

```text
data             ok                15m series=419, files=640, part_live=22
detection_yolo   ok                report=yes; mAP50/mAP50-95/precision/recall keys
judgment         ok                ACTIVE=frozen_tp5_sl2_swap_ma206_20260710; fingerprint=ok
backtest         label_only        pre-holdout/artifact evidence; not final profitability proof
forward          ok                total=0, open=0, closed=0, decision=0/100
jobs             executor_off      ENABLE_JOB_EXECUTOR=0
deploy           vps_executor_off  public VPS; auth from root-only env
```

The single live anomaly is `forward_log_missing`: the MA206 forward file exists but has zero rows. This is a truthful low-sample warning, not a deployment failure.

## Reproduction shape

```bash
bash scripts/deploy_vps.sh
curl http://103.214.174.58:8642/api/pipeline
curl http://103.214.174.58:8642/api/ops/pipeline  # 401 without token
```

Authenticated checks source the root-only VPS env in the remote shell and print status codes only; token values are never printed or stored in evidence.

## Browser evidence

- `.omo/evidence/task-6-vps-screens/desktop_pipeline.png`
- `.omo/evidence/task-6-vps-screens/mobile390_pipeline.png`
- `.omo/evidence/task-6-vps-browser.json`

## Risk and honesty

- Public status is read-only coarse metadata, not a control surface and not a profitability claim.
- Historical backtest evidence is explicitly labeled pre-holdout/artifact evidence.
- Forward is 0/100, so the system has no live forward-return evidence yet.
- No holdout read, threshold/cost/TP/SL change, live order, model promotion, or VPS executor action occurred.
