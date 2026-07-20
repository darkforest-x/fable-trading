# P2.5 Phase 2 — Job Runner（白名单任务）

> **2026-07-20**：已合主线。VPS **必须** `ENABLE_JOB_EXECUTOR=0`（`deploy_vps.sh` 强制）。非当前阻塞项。

**Status (2026-07-10):** Phase 2 **merged to main** (default `ENABLE_JOB_EXECUTOR=0`). Branch history: `feat/p2.5-phase2-job-runner`.

Design: `docs/P2_5_OPS_CONSOLE_DESIGN.md` §4.

## What shipped

| Piece | Role |
|-------|------|
| `src/webapp/jobs/whitelist.py` | Hard-coded job types + param schema + `build_argv` |
| `src/webapp/jobs/store.py` | SQLite under `data/ops_jobs.sqlite` |
| `src/webapp/jobs/runner.py` | FIFO subprocess runner, timeout, cancel, orphan cleanup |
| Thin routes in `server.py` | `/api/ops/jobs*`, `/api/ops/job-types` |
| Dashboard tab **任务** | Form from schema, create, poll status/log, disabled banner |

### Whitelist only

`build_dataset` · `barrier_sweep` · `swap_replication` · `update_okx` · `forward_track` · `deploy_self`

**Never** accepts free `cmd` / `shell` / `argv` strings. **Never** exposes holdout train, YOLO, or auto_label.

## Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `ENABLE_JOB_EXECUTOR` | `0` | `1` required for POST create/cancel |
| `OPS_JOBS_DB` | `data/ops_jobs.sqlite` | sqlite path |
| `OPS_JOB_LOG_DIR` | `logs/jobs` | per-job log files |
| `OPS_MAX_CONCURRENT_JOBS` | `1` | Mac serial by default |
| + Phase 0 auth env | | `OPS_AUTH_MODE` / `OPS_API_TOKEN` |

## Enable executor on Mac

```bash
cd /path/to/fable-trading
export ENABLE_JOB_EXECUTOR=1
# recommended when not purely local:
# export OPS_AUTH_MODE=token
# export OPS_API_TOKEN='<OWNER_SET>'
export PYTHONPATH=.
python3 -m uvicorn src.webapp.server:app --host 127.0.0.1 --port 8643
```

Open http://127.0.0.1:8643 → tab **任务**.

**VPS:** keep `ENABLE_JOB_EXECUTOR=0` (systemd should force this). POST returns 403.

## APIs

| Route | Auth | Notes |
|-------|------|--------|
| `GET /api/ops/job-types` | ops auth when required | schema for forms + `executor_enabled` |
| `GET /api/ops/jobs` | ops | history |
| `GET /api/ops/jobs/{id}` | ops | detail + log tail |
| `GET /api/ops/jobs/{id}/log` | ops | plain text tail |
| `POST /api/ops/jobs` | ops + executor | body: `{job_type, params}` only |
| `POST /api/ops/jobs/{id}/cancel` | ops + executor | SIGTERM → SIGKILL |

## Tests

```bash
PYTHONPATH=. python3 -m pytest tests/test_ops_jobs_phase2.py tests/test_ops_phase01.py -q
```

## Merge into main

```bash
# from main worktree
cd ~/fable-trading
git fetch origin
git merge --ff-only feat/p2.5-phase2-job-runner
# or: git merge feat/p2.5-phase2-job-runner
```
