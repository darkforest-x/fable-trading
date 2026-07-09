"""FastAPI routes for the fable-trading dashboard.

Routes are intentionally thin: `dashboard_payloads` owns universe-scoped
overview/backtest/chart JSON, and `forward_payloads` owns forward validation
JSON. Static assets are mounted last so API routes stay reachable.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.backtest.run import BASE_COST
from src.webapp.dashboard_cache import DEFAULT_UNIVERSE
from src.webapp.dashboard_payloads import (
    backtest_payload,
    chart_payload,
    overview_payload,
    symbols_payload,
    trade_rows_payload,
)
from src.webapp.forward_payloads import FORWARD_COST, forward_payload

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


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
