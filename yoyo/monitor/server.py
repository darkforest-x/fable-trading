"""Loopback-only FastAPI frontend/API with an independent background scanner."""
from __future__ import annotations

from contextlib import asynccontextmanager
import fcntl
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from yoyo.monitor import FRESH_MS, MODEL_KIND, MODEL_PROTOCOL, MONITORED_TIMEFRAMES, SIGNAL_KIND, SIGNAL_PROTOCOL
from yoyo.monitor.service import Monitor
from yoyo.monitor.store import Store
from yoyo.monitor.tradingview import DesktopOpenError, open_chart

STATIC = Path(__file__).parent / "static"
DEFAULT_RUNTIME = Path.home() / "Library/Application Support/Fable/ImpulseMonitor"


class DesktopChartRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=48)
    timeframe: str = Field(min_length=1, max_length=5)


def create_app(runtime=None, start_monitor=True):
    runtime = Path(runtime or os.environ.get("FABLE_IMPULSE_RUNTIME", DEFAULT_RUNTIME))
    store = Store(runtime / "monitor.sqlite3")
    monitor = Monitor(store)

    @asynccontextmanager
    async def lifespan(app):
        lockfile = (runtime / "service.lock").open("a+")
        try:
            fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if start_monitor:
                monitor.start()
            yield
        finally:
            monitor.close()
            lockfile.close()

    app = FastAPI(title="Fable Impulse Monitor", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.state.monitor = monitor

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    def status():
        return monitor.status()

    @app.get("/api/health")
    @app.get("/healthz")
    def health():
        state = monitor.status()
        scan = state["scan"]
        last = scan.get("finished_at_ms")
        state["service_alive"] = True
        state["market_ready"] = bool(last and state["now_ms"] - last < 20 * 60000
                                     and scan.get("total", 0) > scan.get("errors", 0)
                                     and scan.get("status") not in ("error", "starting"))
        state["model_ready"] = state["runtime"]["model_gate"]["status"] == "ready"
        state["ok"] = state["market_ready"] and state["model_ready"] and scan.get("errors", 0) == 0
        return state

    @app.get("/api/signals")
    def signals(limit: int = Query(200, ge=1, le=2000), symbol: str = None, timeframe: str = None,
                kind: str = MODEL_KIND, side: str = None, source: str = None, confirmation: str = None):
        if kind not in (MODEL_KIND, SIGNAL_KIND):
            raise HTTPException(400, "支持指标启动或 YOLO 确认信号。")
        direct = kind == SIGNAL_KIND
        protocol = SIGNAL_PROTOCOL if direct else MODEL_PROTOCOL
        rows = store.list_events(limit, symbol, timeframe, kind, side, protocol=protocol, direct_only=direct)
        if source not in (None, "live", "replay") or confirmation not in (None, "raw", "yolo", "raw_yolo"):
            raise HTTPException(400, "unsupported source or confirmation")
        rows = [row for row in rows if (source is None or row.get("source") == source)
                and (confirmation is None or row.get("confirmation") == confirmation)]
        for row in rows:
            row["is_fresh"] = 0 <= monitor.client.clock() - row["bar_close_ms"] <= FRESH_MS
        return {"items": rows, "total": len(rows), "kind": kind, "protocol": protocol,
                "source": source, "confirmation": confirmation}

    @app.get("/api/candidates")
    def candidates(limit: int = Query(200, ge=1, le=2000), symbol: str = None, timeframe: str = None):
        rows = store.list_candidates(limit, symbol, timeframe)
        return {"items": rows, "total": sum(store.candidate_counts().values()),
                "counts": store.candidate_counts()}

    @app.get("/api/markets")
    def markets():
        rows = monitor.markets()
        return {"items": rows, "total": len(rows)}

    @app.get("/api/chart")
    def chart(symbol: str, timeframe: str = "1H"):
        if timeframe not in MONITORED_TIMEFRAMES:
            raise HTTPException(400, "unsupported timeframe")
        result = monitor.chart(symbol, timeframe)
        if result is None:
            raise HTTPException(404, "该交易对正在初始化，请稍后刷新。")
        return result

    @app.post("/api/tradingview/open")
    def tradingview_open(payload: DesktopChartRequest, request: Request):
        # UI activation is a same-origin POST, never a side effect of a GET,
        # chart render, scanner tick, or an unrelated website's request.
        expected_origin = f"{request.url.scheme}://{request.url.netloc}"
        if (request.headers.get("origin") != expected_origin
                or request.headers.get("x-spike-action") != "open-tradingview"
                or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"):
            raise HTTPException(403, "请从本机 spike 页面点击查看结构。")
        try:
            return open_chart(payload.symbol, payload.timeframe)
        except DesktopOpenError as error:
            raise HTTPException(error.status_code, str(error)) from error

    app.mount("/static", StaticFiles(directory=str(STATIC), check_dir=False), name="static")
    return app


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
