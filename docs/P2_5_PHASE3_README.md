# P2.5 Phase 3 — Data + Model Hubs（只读）

**Status (2026-07-10):** Phase 3 **read-only** hubs on branch `feat/p2.5-phase3-data-model-hub`.

Design: `docs/P2_5_OPS_CONSOLE_DESIGN.md` §5（数据/模型页只读部分；**不含** promote POST）。

## What shipped

| Piece | Role |
|-------|------|
| `src/webapp/data_hub.py` | Coverage by bar (`list_series` + raw csv counts), embed `data_audit_summary.json`, forward log health |
| `src/webapp/model_hub.py` | List `models/frozen_*.json` + `.txt` pair check, threshold/sha display, ACTIVE pointer, fingerprint best-effort |
| Thin routes in `server.py` | `GET /api/ops/data-hub`, `GET /api/ops/model-hub` via `verify_ops_request` |
| Dashboard tabs **数据** / **模型** | Read-only tables + tiles |
| Tests | `tests/test_ops_data_model_hub.py` (tmp_path, no network) |

### Explicitly not in this PR

- `POST /api/ops/models/promote` (ACTIVE write) — deferred
- Config page / change-request diff
- Holdout evaluation UI
- Any change to default `ENABLE_JOB_EXECUTOR=0`

## APIs

| Route | Auth | Notes |
|-------|------|--------|
| `GET /api/ops/data-hub` | ops auth when required | coverage + audit + forward health |
| `GET /api/ops/model-hub` | ops auth when required | frozen list + ACTIVE; `promote_available: false` |

## Environment

Same as Phase 0–2:

| Variable | Default | Meaning |
|----------|---------|---------|
| `OPS_AUTH_MODE` / `OPS_API_TOKEN` | off / empty | protect `/api/ops/*` |
| `ENABLE_JOB_EXECUTOR` | `0` | unchanged; hubs are GET-only |
| `OPS_ACTIVE_MODEL_POINTER` | `models/ACTIVE` | optional path override for ACTIVE text pointer |

## Tests

```bash
cd /path/to/fable-trading
PYTHONPATH=. python3 -m pytest tests/test_ops_data_model_hub.py tests/test_ops_phase01.py tests/test_ops_jobs_phase2.py -q
```

## Merge into main

```bash
# from main worktree
cd ~/fable-trading
git fetch origin
git merge --ff-only feat/p2.5-phase3-data-model-hub
# or: git merge feat/p2.5-phase3-data-model-hub
```
