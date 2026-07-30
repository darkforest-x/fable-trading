# Todo 5 — P2.5 local verify and harden

**Result: PASS (initial tests + loopback API; final browser acceptance followed)**
**When:** 2026-07-10
**Branch:** `codex/grok-2day`

> Follow-up: commit `7c0839d` completed desktop and 390px browser QA with zero
> console errors or warnings. The final evidence is
> `analysis/p25_local_acceptance_20260710.md`; the deferred note below describes
> only this earlier baseline run.

## Hypothesis

Focused ops suites are green on this branch, and a loopback server with
`OPS_AUTH_MODE=token` + temporary `OPS_API_TOKEN` + `ENABLE_JOB_EXECUTOR=0`
fail-closes unauth ops reads, serves authenticated hubs, and rejects job POSTs.

## Predeclared pass/fail

| Criterion | Result |
|-----------|--------|
| Focused pytest green | PASS **58 passed** in 37.79s |
| Unauth protected ops → 401 | PASS (data-hub, experiments) |
| Auth ops status / hubs → 200 | PASS; `token_configured=true`, `executor_enabled=false` |
| POST /api/ops/jobs with executor=0 | PASS **403** disabled |
| Wrong token → 401 | PASS |
| Executor remains off | PASS |
| Browser 390px / console | **DEFERRED** — Playwright not installed; static `index.html`/`app.js` HTTP 200 only |

## Commands

```bash
python3 -m venv .venv_p25_qa   # untracked local QA env
.venv_p25_qa/bin/pip install pytest fastapi 'uvicorn[standard]' httpx pandas lightgbm numpy scikit-learn

PYTHONPATH=. .venv_p25_qa/bin/python -m pytest \
  tests/test_ops_phase01.py tests/test_ops_jobs_phase2.py \
  tests/test_ops_phase3_hubs.py tests/test_ops_data_model_hub.py -q

OPS_AUTH_MODE=token OPS_API_TOKEN='…temp…' ENABLE_JOB_EXECUTOR=0 \
  PYTHONPATH=. .venv_p25_qa/bin/python -m uvicorn src.webapp.server:app \
  --host 127.0.0.1 --port 18765
# curl matrix (see Results)
```

## Results (loopback curl)

| Call | HTTP | Notes |
|------|-----:|-------|
| GET /api/ops/status (no auth) | 200 | Public status; shows auth required + executor false |
| GET /api/ops/data-hub (no auth) | 401 | Fail-closed |
| GET /api/ops/experiments (no auth) | 401 | Fail-closed |
| GET /api/ops/status (Bearer) | 200 | token_configured true, executor false |
| GET /api/ops/data-hub (Bearer) | 200 | keys: audit, coverage, forward, read_only, … |
| GET /api/ops/model-hub (Bearer) | 200 | promote_available present; read_only |
| POST /api/ops/jobs (Bearer, executor=0) | 403 | 执行器禁用 |
| GET /api/ops/data-hub (wrong token) | 401 | |
| GET / (index) + /app.js | 200 | Static shell only |

## Comparison vs baseline

| | Before this slot | After |
|--|------------------|-------|
| P2.5 evidence on grok-2day | none | 58 tests + curl matrix |
| Local QA env | missing pytest/fastapi/lightgbm in main `.venv` | untracked `.venv_p25_qa` |
| Code changes | — | **none** (green path; commit N per plan) |

## Bottleneck / next hypothesis

- Importing `src.webapp.server` pulls `lightgbm`+`sklearn` via judgment/backtest
  chain — local QA needs a fuller venv than dashboard-only deps. Optional later:
  lazy/TYPE_CHECKING decoupling so ops-only tests stay light.
- Browser QA (Playwright desktop + 390px) still open for a follow-up atom.
- Todo 6 (VPS redacted pipeline view) unblocked after this.

## Risk / honesty

- Did not promote models, enable executor, or hit VPS ops.
- Temp token only in shell env for this run; not written to tracked files.
- `.venv_p25_qa` is untracked and not a project dependency pin.
- Full browser visual QA not executed — acceptance partially deferred.
