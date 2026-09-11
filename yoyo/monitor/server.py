"""Loopback-only FastAPI frontend/API with an independent background scanner."""
from __future__ import annotations

from contextlib import asynccontextmanager
import fcntl
import logging
import os
from pathlib import Path
import time

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
LOG = logging.getLogger("fable.monitor.dispatch")
DISPATCH_TRACE = os.environ.get("FABLE_MONITOR_DISPATCH_TRACE") == "1"


def dispatch_trace(marker: str, *, started_ns: int | None = None) -> None:
    """Temporarily expose route-dispatch timing without logging query strings."""
    if DISPATCH_TRACE:
        elapsed = "" if started_ns is None else f" elapsed_ms={(time.monotonic_ns() - started_ns) // 1_000_000}"
        LOG.warning("dispatch marker=%s%s", marker, elapsed)


class DesktopChartRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=48)
    timeframe: str = Field(min_length=1, max_length=5)


class ReplayChartEndpointUnavailable(ValueError):
    """Translate the optional frozen-history loader error at the API edge."""


def replay_chart_result(event: dict) -> dict:
    """Load frozen replay OHLC only for a selected historical chart request.

    ``replay_chart`` depends on pandas/numpy and frozen evaluation features.
    Keeping that optional display dependency out of the FastAPI import path lets
    the listener become available before an operator opens a replay card.
    """
    from yoyo.monitor.replay_chart import ReplayChartUnavailable, load_replay_chart

    try:
        return load_replay_chart(event)
    except ReplayChartUnavailable as error:
        raise ReplayChartEndpointUnavailable(error.code) from error


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
        started_ns = time.monotonic_ns()
        dispatch_trace("entry:" + request.url.path)
        response = await call_next(request)
        dispatch_trace("exit:" + request.url.path, started_ns=started_ns)
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
        dispatch_trace("handler:/api/status")
        return monitor.status()

    @app.get("/api/health")
    @app.get("/healthz")
    def health():
        return monitor.health()

    @app.get("/api/signals")
    def signals(limit: int = Query(200, ge=1, le=2000), symbol: str = None, timeframe: str = None,
                kind: str = None, side: str = None, source: str = None, confirmation: str = None,
                before_close_ms: int = None, before_id: str = None):
        started_ns = time.monotonic_ns()
        dispatch_trace("handler:/api/signals")
        if source not in (None, "live", "replay") or confirmation not in (None, "raw", "yolo", "raw_yolo"):
            raise HTTPException(400, "unsupported source or confirmation")
        # The V1 UI filters by confirmation.  Infer its event kind when the
        # legacy `kind` parameter is omitted, rather than silently querying
        # only YOLO rows for `confirmation=raw`.
        if kind is None:
            kind = SIGNAL_KIND if confirmation == "raw" else MODEL_KIND if confirmation == "yolo" else None
        if kind not in (None, MODEL_KIND, SIGNAL_KIND):
            raise HTTPException(400, "支持指标启动或 YOLO 确认信号。")
        direct = kind == SIGNAL_KIND
        protocol = SIGNAL_PROTOCOL if kind == SIGNAL_KIND else MODEL_PROTOCOL if kind == MODEL_KIND else None
        if (before_close_ms is None) != (before_id is None):
            raise HTTPException(400, "cursor requires both close time and event id")
        rows = store.list_events(limit, symbol, timeframe, kind, side, protocol=protocol,
                                 source=source, confirmation=confirmation,
                                 before_close_ms=before_close_ms, before_id=before_id)
        # Trace only phase names and row counts.  It deliberately excludes
        # request filters, event identities, and response content.
        dispatch_trace(f"signals:rows={len(rows)}", started_ns=started_ns)
        for row in rows:
            row["is_fresh"] = row.get("source") == "live" and 0 <= monitor.client.clock() - row["bar_close_ms"] <= FRESH_MS
        dispatch_trace("signals:freshness", started_ns=started_ns)
        cursor = ({"close_ms": rows[-1]["bar_close_ms"], "event_id": rows[-1]["id"]}
                  if len(rows) == limit else None)
        dispatch_trace(f"signals:return={len(rows)}", started_ns=started_ns)
        return {"items": rows, "total": len(rows), "kind": kind, "protocol": protocol,
                "source": source, "confirmation": confirmation, "next_cursor": cursor}

    @app.get("/api/candidates")
    def candidates(limit: int = Query(200, ge=1, le=2000), symbol: str = None, timeframe: str = None):
        rows = store.list_candidates(limit, symbol, timeframe)
        return {"items": rows, "total": sum(store.candidate_counts().values()),
                "counts": store.candidate_counts()}

    @app.get("/api/markets")
    def markets():
        # A newly introduced summary table is populated by the scanner, never
        # by HTTP.  Reject its short initialization window so a Watch tab does
        # not cache an empty successful response from legacy full rows.
        backfill = store.get_meta("market_summary_backfill", {})
        if backfill.get("status") in {"starting", "running"}:
            raise HTTPException(503, "市场观察摘要正在初始化，请稍后重试。")
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

    @app.get("/api/replay/chart")
    def replay_chart(event_id: str = Query(min_length=24, max_length=24, pattern="^[0-9a-f]{24}$")):
        event = store.get_event(event_id)
        if event is None:
            raise HTTPException(404, "未找到历史回放信号。")
        try:
            return replay_chart_result(event)
        except ReplayChartEndpointUnavailable as error:
            raise HTTPException(404, "该历史信号缺少可核验的冻结 OHLC：" + str(error)) from error

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
