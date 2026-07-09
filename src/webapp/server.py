"""FastAPI routes for the fable-trading dashboard.

Routes are intentionally thin: `dashboard_payloads` owns universe-scoped
overview/backtest/chart JSON, and `forward_payloads` owns forward validation
JSON. P2.5 Phase 0+1 adds ops auth + experiment registry + agenda (read-only).
Static assets are mounted last so API routes stay reachable.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.backtest.run import BASE_COST
from src.webapp.agenda_payloads import agenda_payload
from src.webapp.auth import verify_ops_request
from src.webapp.dashboard_cache import DEFAULT_UNIVERSE
from src.webapp.dashboard_payloads import (
    backtest_payload,
    chart_payload,
    overview_payload,
    symbols_payload,
    trade_rows_payload,
)
from src.webapp.experiment_registry import experiment_detail, list_experiments
from src.webapp.forward_payloads import FORWARD_COST, forward_payload
from src.webapp.ops_flags import ops_status_payload

app = FastAPI(title="fable-trading dashboard")


@app.middleware("http")
async def no_cache_static(request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/overview")
def overview(universe: str = DEFAULT_UNIVERSE) -> dict:
    return overview_payload(universe)


@app.get("/api/backtest")
def backtest(cost: float = BASE_COST, universe: str = DEFAULT_UNIVERSE) -> dict:
    return backtest_payload(cost=cost, universe=universe)


@app.get("/api/trades")
def trade_rows(
    window: str = "accept",
    limit: int = 1000,
    cost: float = BASE_COST,
    symbol: str = "",
    universe: str = DEFAULT_UNIVERSE,
) -> list[dict]:
    return trade_rows_payload(window=window, limit=limit, cost=cost, symbol=symbol, universe=universe)


@app.get("/api/symbols")
def symbols(universe: str = DEFAULT_UNIVERSE) -> list[dict]:
    return symbols_payload(universe)


@app.get("/api/chart/{source}/{symbol}")
def chart(source: str, symbol: str, bars: int = 3000, universe: str = DEFAULT_UNIVERSE) -> dict:
    return chart_payload(source=source, symbol=symbol, bars=bars, universe=universe)


@app.get("/api/forward")
def forward(cost: float = FORWARD_COST) -> dict:
    return forward_payload(cost)


# ---------- P2.5 Phase 0+1: ops (read-only) ----------


@app.get("/api/ops/status")
def ops_status() -> dict:
    """Public: whether ops auth is required (does not leak the token)."""
    return ops_status_payload()


@app.get("/api/ops/experiments")
def ops_experiments(
    request: Request,
    kind: str = "",
    q: str = "",
    sort: str = Query(default="mtime", pattern="^(mtime|val_auc|perm_p)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> dict:
    verify_ops_request(request)
    return list_experiments(kind=kind, q=q, sort=sort, order=order)


@app.get("/api/ops/experiments/{exp_id}")
def ops_experiment_detail(exp_id: str, request: Request):
    verify_ops_request(request)
    detail = experiment_detail(exp_id)
    if detail is None:
        return JSONResponse({"detail": f"experiment not found: {exp_id}"}, status_code=404)
    return detail


@app.get("/api/ops/agenda")
def ops_agenda(request: Request) -> dict:
    verify_ops_request(request)
    return agenda_payload()


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
