# Todo 6 (local atom) — redacted pipeline status surface

**Result: PASS (local API + UI shell; VPS deploy deferred)**  
**When:** 2026-07-10  
**Branch:** `codex/grok-2day`

## Hypothesis

A single ops-auth GET `/api/ops/pipeline` can compose coarse stage rows (data,
YOLO diagnostic, judgment ACTIVE, backtest evidence label, forward sample,
jobs executor, deploy) without secrets, absolute local paths, or write actions.

## Predeclared pass/fail

| Criterion | Result |
|-----------|--------|
| Payload has 7 stages | PASS |
| `read_only=true`, `write_actions=[]` | PASS |
| `executor_enabled=false` default | PASS |
| No absolute `/Users/` path leak | PASS |
| No temp token echo | PASS |
| Unauth → 401 | PASS |
| Auth → 200 | PASS |
| Tests | PASS `tests/test_ops_pipeline_status.py` (+ related still green) |
| UI tab present | PASS “流水线” in `index.html` / `app.js` |
| VPS browser deploy | **DEFERRED** (next atom) |

## Commands

```bash
PYTHONPATH=. .venv_p25_qa/bin/python -m pytest tests/test_ops_pipeline_status.py -q

OPS_AUTH_MODE=token OPS_API_TOKEN='…' ENABLE_JOB_EXECUTOR=0 \
  PYTHONPATH=. .venv_p25_qa/bin/python -m uvicorn src.webapp.server:app \
  --host 127.0.0.1 --port 18766

curl -s -H "Authorization: Bearer …" http://127.0.0.1:18766/api/ops/pipeline
```

## Real surface snapshot

```
stages: data:ok, detection_yolo:ok, judgment:ok, backtest:label_only,
        forward:ok, jobs:executor_off, deploy:local_ops
executor_enabled: false
violations: []
```

## Comparison vs baseline

| | Before | After |
|--|--------|-------|
| E2E pipeline API | none (only hubs/status pieces) | `/api/ops/pipeline` |
| Ops UI | data/models/jobs tabs | + 流水线 tab |
| Redaction tests | hub-level only | dedicated pipeline safety asserts |

## Bottleneck / next hypothesis

VPS deploy via existing `scripts/deploy_vps.sh` + browser QA on
`http://103.214.174.58:8642` is the remaining Todo 6 acceptance slice. Keep
executor=0; redacted public output only.

Owner LS review still blocks annotation writeback promote.

## Risk / honesty

- YOLO stage reads existing report/metrics files only — diagnostic, not E2.1b gate.
- Forward stage may call heavier `forward_payload` when log exists (shared with data hub).
- No Playwright 390px pass in this atom.
- Did not deploy VPS or change ACTIVE/champion.
