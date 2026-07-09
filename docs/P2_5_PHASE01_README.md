# P2.5 Phase 0+1 — Ops Auth + Experiment Registry + Agenda

**Status (2026-07-10):** Phase 0+1 implemented (read-only). **No job runner** yet (Phase 2).

Design details: `docs/P2_5_OPS_CONSOLE_DESIGN.md`.

## What shipped

| Piece | Role |
|-------|------|
| `src/webapp/auth.py` | Bearer / `X-Ops-Token` check for `/api/ops/*` |
| `src/webapp/ops_flags.py` | Env flags (`OPS_AUTH_MODE`, `OPS_API_TOKEN`, executor off) |
| `src/webapp/experiment_registry.py` | Scan `analysis/output/*.json` → list/detail |
| `src/webapp/agenda_payloads.py` | Read-only `docs/RESEARCH_AGENDA.md` |
| Dashboard tabs **实验** / **议程** | Static UI in `index.html` + `app.js` |

### APIs

| Route | Auth | Notes |
|-------|------|--------|
| `GET /api/ops/status` | Public | Does not leak token; reports `ops_auth_required` |
| `GET /api/ops/experiments` | Ops auth when required | Filter/sort experiment index |
| `GET /api/ops/experiments/{id}` | Ops auth when required | Detail + linked report path |
| `GET /api/ops/agenda` | Ops auth when required | Agenda markdown payload |

Existing overview/backtest/chart APIs are unchanged (still open unless you protect at nginx).

## Environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `OPS_AUTH_MODE` | `off` | `off` \| `token` \| `nginx` (`nginx` = document that edge auth is external) |
| `OPS_API_TOKEN` | empty | Secret for Bearer / `X-Ops-Token`. **Never commit.** |
| `OPS_REQUIRE_AUTH` | `0` | `1` forces ops auth even outside pure `token` mode |
| `ENABLE_JOB_EXECUTOR` | `0` | Must stay `0` until Phase 2 |

### Local (Mac, default open)

```bash
# optional: leave auth off for localhost
export OPS_AUTH_MODE=off
python3 -m uvicorn src.webapp.server:app --host 127.0.0.1 --port 8642
# or double-click 启动看板.command
```

### Token mode (recommended before any public/VPS ops surface)

```bash
export OPS_AUTH_MODE=token
export OPS_API_TOKEN='<OWNER_SET_HIGH_ENTROPY_SECRET>'
python3 -m uvicorn src.webapp.server:app --host 127.0.0.1 --port 8642
```

Clients send either:

- `Authorization: Bearer <token>`, or
- `X-Ops-Token: <token>`

If `OPS_AUTH_MODE=token` but `OPS_API_TOKEN` is empty → ops routes return **503** (fail closed).

## Run the dashboard

```bash
cd /path/to/fable-trading
PYTHONPATH=. python3 -m uvicorn src.webapp.server:app --host 127.0.0.1 --port 8642
```

Open: http://127.0.0.1:8642

> Avoid binding public `0.0.0.0` without token or nginx auth.

## Open 实验 / 议程 tabs

1. Start the dashboard and open the URL above.
2. Top nav: **实验** (experiment registry) or **议程** (research agenda).
3. If `OPS_AUTH_MODE=token`, the top bar shows an **OPS token** field — paste the secret, click **保存** (stored in `sessionStorage` only).
4. **实验**: sort/filter rows from `analysis/output/*.json`; click a row for detail JSON / report path.
5. **议程**: renders `docs/RESEARCH_AGENDA.md` (read-only).

## VPS note

**Set token (or nginx basic-auth) before exposing the board publicly.**

- Minimum for ops JSON: `OPS_AUTH_MODE=token` + non-empty `OPS_API_TOKEN` in the systemd/env unit.
- Prefer loopback bind + reverse proxy; optional nginx snippet: `docs/ops_nginx_snippet.conf.example`.
- Keep `ENABLE_JOB_EXECUTOR=0` on VPS until Phase 2 is intentionally enabled on Mac-only.
- Agent never writes real secrets into the repo; owner generates the token offline.

## Tests

```bash
PYTHONPATH=. python3 -m pytest tests/test_ops_phase01.py -q
```

## Out of scope (this phase)

- Job runner / whitelist subprocess (Phase 2)
- Data hub / model hub write paths (Phase 3)
- Holdout eval, YOLO retrain, `auto_label` / `MAX_DENSE_BARS` changes
