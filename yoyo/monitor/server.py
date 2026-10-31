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
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.gzip import GZipMiddleware
from yoyo.monitor.asset_delivery import cache_policy, render_index, boot_bundle, BOOT_PATH, style_bundles, coin_root

from yoyo.monitor import FRESH_MS, MODEL_KIND, MODEL_PROTOCOL, MONITORED_TIMEFRAMES, SIGNAL_KIND, SIGNAL_PROTOCOL, SHORT_SIGNAL_PROTOCOL
from yoyo.monitor import market_sync as market_sync_research
from yoyo.monitor.market_sync_okx import Observer as MarketSync
from yoyo.monitor.market_context import MarketContext
from yoyo.monitor.service import Monitor
from yoyo.monitor.shadow_api import (SHADOW_DATABASE, ShadowBookUnavailable, events as shadow_events,
                                     market_snapshots as shadow_market_snapshots,
                                     status as shadow_status)
from yoyo.monitor.spike_lines_api import (LinesUnavailable, database as lines_database, events as lines_events,
                                         ledger as lines_ledger, status as lines_status)
from yoyo.monitor.store import Store
from yoyo.monitor.tradingview import DesktopOpenError, open_chart

STATIC = Path(__file__).parent / "static"
DEFAULT_RUNTIME = Path.home() / "Library/Application Support/Fable/ImpulseMonitor"
LOG = logging.getLogger("fable.monitor.dispatch")
DISPATCH_TRACE = os.environ.get("FABLE_MONITOR_DISPATCH_TRACE") == "1"


def dispatch_trace(marker: str, *, started_ns: int | None = None, elapsed_ms: int | None = None) -> None:
    """Temporarily expose route-dispatch timing without logging query strings."""
    if DISPATCH_TRACE:
        if elapsed_ms is not None:
            elapsed = f" elapsed_ms={elapsed_ms}"
        else:
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
    shadow_book = runtime / SHADOW_DATABASE
    lines_book = lines_database(runtime)
    monitor = Monitor(store)
    market_sync = MarketSync(runtime)
    market_context = MarketContext(runtime, market_sync.client)
    from yoyo.monitor.altcoin_leader_service import LeaderService, install as install_leaders
    altcoin_leaders = LeaderService(runtime)
    from yoyo.monitor.vidya_retest_api import VidyaRetestService
    vidya_retest = VidyaRetestService(runtime)
    from yoyo.monitor.monday_fib_service import MondayFibService, install as install_monday_fib
    monday_fib = MondayFibService(runtime)
    from yoyo.monitor.monday_reclaim_paper import ReclaimPaper, install as install_reclaim_paper
    reclaim_paper = ReclaimPaper(runtime, monday_fib)
    monday_fib.listeners.append(reclaim_paper)
    monday_fib.notification_score_lookup = reclaim_paper.notification_score
    from yoyo.monitor.spike_alert import SpikeAlert, install as install_spike_alert
    spike_alert = SpikeAlert(runtime)  # owner 2026-10-27: notify only, never touches positions
    monday_fib.listeners.append(spike_alert)

    @asynccontextmanager
    async def lifespan(app):
        lockfile = (runtime / "service.lock").open("a+")
        try:
            fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if start_monitor:
                monitor.start()
                # Owner retired the market workspace on 2026-10-23.
                # Keep shared quotes, Monday monitoring and execution independent.
                altcoin_leaders.start()
                vidya_retest.start()
                monday_fib.start()
                reclaim_paper.start()
                spike_alert.start()
            if start_monitor:
                app.state.start_yolo_workbench()
            app.state.paper_desk_market.start()
            app.state.paper_desk_worker.start()
            yield
        finally:
            app.state.monday_candles.close()
            app.state.market_workstation.close()
            app.state.signal_read_cache.close()
            app.state.okx_execution_dashboard.close()
            app.state.yolo_workbench_supervisor.close()
            app.state.paper_desk_worker.close()
            app.state.paper_desk_market.close()
            vidya_retest.close()
            reclaim_paper.close()
            spike_alert.close()
            monday_fib.close()
            market_context.close()
            market_sync.close()
            altcoin_leaders.close()
            monitor.close()
            lockfile.close()

    app = FastAPI(title="Fable Impulse Monitor", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.state.monitor = monitor
    from yoyo.monitor.market_workstation import install as install_market_workstation
    install_market_workstation(app)
    from yoyo.monitor.signal_cache import SignalReadCache
    app.state.signal_read_cache = SignalReadCache(store)
    app.state.altcoin_leaders = altcoin_leaders
    install_leaders(app, altcoin_leaders)

    from yoyo.monitor.notification_api import install as install_notifications
    install_notifications(app, runtime, monitor)
    from yoyo.monitor.copier_proxy import install as install_copier
    install_copier(app)
    from yoyo.okx_execution.dashboard import install as install_okx_dashboard
    install_okx_dashboard(app)
    from yoyo.monitor.vidya_retest_api import install as install_vidya_retest
    install_vidya_retest(app, runtime, vidya_retest)
    install_monday_fib(app, monday_fib)
    install_reclaim_paper(app, reclaim_paper)
    install_spike_alert(app, spike_alert)
    from yoyo.monitor.monday_candles import install as install_monday_candles
    install_monday_candles(app, runtime)
    from yoyo.monitor.strategy_backtest import install as install_strategy_backtest
    install_strategy_backtest(app)

    @app.middleware("http")
    async def headers(request, call_next):
        started_ns = time.monotonic_ns()
        dispatch_trace("entry:" + request.url.path)
        response = await call_next(request)
        dispatch_trace("exit:" + request.url.path, started_ns=started_ns)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = cache_policy(request.url.path, request.query_params.get("v"), STATIC, response.status_code)
        return response

    @app.get("/")
    def index():
        return HTMLResponse(render_index(STATIC))

    @app.get(BOOT_PATH)
    def workspace_boot():
        return Response(boot_bundle(STATIC)[0], media_type='text/javascript')

    @app.get('/static/workspace-{name}.css')
    def workspace_styles(name: str):
        bundle = style_bundles(STATIC).get(name)
        if bundle is None:
            # Only the three generated names belong to this route. Preserve
            # existing workspace-*.css URLs used by standalone study pages.
            if name.isascii() and name.replace('-', '').isalnum():
                path = STATIC / f'workspace-{name}.css'
                if path.is_file():
                    return FileResponse(path, media_type='text/css')
            raise HTTPException(404, 'stylesheet_not_found')
        return Response(bundle[0], media_type='text/css')

    @app.get("/api/status")
    def status():
        dispatch_trace("handler:/api/status")
        return monitor.status()

    @app.get('/api/candles/status')
    def candle_status(symbol: str = Query('', max_length=64), limit: int = Query(80, ge=1, le=200)):
        return monitor.client.hub.snapshot(symbol=symbol, limit=limit)

    @app.get("/api/market-sync")
    def market_sync_snapshot():
        value = market_sync.snapshot()
        return dict(value, status=dict(value['status'], state='stopped', next_ms=None),
                    monitoring_enabled=False, stopped_reason="owner_retired_market_workspace",
                    workers=dict(sync=bool(market_sync._thread and market_sync._thread.is_alive()),
                                 context=bool(market_context._thread and market_context._thread.is_alive())))

    @app.get('/api/market-sync/context')
    def market_context_snapshot(
        zone: str = Query('America/New_York', pattern=r'^(America/New_York|UTC|Asia/Shanghai)$'),
        symbol: str = Query('BTC-USDT-SWAP', pattern=r'^[A-Z0-9]{1,24}-USDT-SWAP$'),
    ):
        raise HTTPException(410, "市场页监控已停用")

    @app.get("/api/market-sync/history")
    def market_sync_history(group: str = Query(None, max_length=64)):
        try:
            return market_sync_research.history(group)
        except FileNotFoundError:
            raise HTTPException(status_code=503, detail="market_sync_history_missing") from None
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown_group") from None

    @app.get("/api/market-sync/iterations")
    def market_sync_iterations():
        return {"items": market_sync_research.iterations()}

    @app.get("/api/v13/signals")
    def v13_signals(limit: int = Query(100, ge=1, le=1000), symbol: str = None, timeframe: str = None):
        from yoyo.contracts.signal_universe import exclusion_reason, public_policy
        from yoyo.monitor.v130_policy import TIMEFRAMES as periods, observation_control
        from yoyo.monitor.v130_signals import SIGNAL_PROTOCOL as protocol, SIGNAL_KIND as kind
        if timeframe is not None and timeframe not in periods:
            raise HTTPException(400, "不支持的周期。")
        arm = store.get_meta("notification_policy:spike-burst-v130-retest-notifications-v1", {})
        rows = store.list_events(limit=limit, symbol=symbol, timeframe=timeframe, kind=kind,
                                 protocol=protocol, source="live", confirmation="retest", summary=False)
        clock = monitor.client.clock()
        control = observation_control(store)
        for row in rows:
            reason = ("v130_paused_by_owner" if control.get("status") == "paused" else
                      exclusion_reason(row['symbol'], timeframe=row['timeframe'], side=row['side']))
            row.update(entry_eligible=reason is None, entry_exclusion=reason,
                       is_fresh=reason is None and 0 <= clock - row["bar_close_ms"] <= FRESH_MS)
        return {"items": rows, "protocol": protocol, "timeframes": list(periods),
                "entry_universe": public_policy(),
                "candidate_source": "ordinary_both", "activation": arm, "observation_control": control,
                "signals_24h": store.count_since(clock - 86400000, kind=kind, protocol=protocol),
                "last_observation": store.get_meta("v130:last_observation", {}),
                "now_ms": clock, "orders_enabled": False, "chart_parity_verified": False}

    @app.get("/api/health")
    @app.get("/healthz")
    def health():
        return monitor.health()

    @app.get("/api/signal-ledger")
    def signal_ledger(source: str = "live", confirmation: str = "raw", period: str = "all",
                      timeframe: str = None, side: str = None, search: str = "", outcome: str = "all",
                      sort: str = "newest", offset: int = Query(0, ge=0), limit: int = Query(24, ge=1, le=2000)):
        from yoyo.monitor.signal_analytics import ledger
        try:
            return ledger(app.state.signal_read_cache, now=monitor.client.clock(), source=source, confirmation=confirmation,
                          period=period, timeframe=timeframe, side=side, search=search,
                          outcome=outcome, sort=sort, offset=offset, limit=limit)
        except ValueError as exc:
            raise HTTPException(400, "不支持的统计筛选条件。") from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/api/signals")
    def signals(limit: int = Query(200, ge=1, le=2000), symbol: str = None, timeframe: str = None,
                kind: str = None, side: str = None, source: str = None, confirmation: str = None,
                before_close_ms: int = None, before_id: str = None, view: str = None,
                period: str = "all", search: str = "", outcome: str = "all", sort: str = "newest",
                offset: int = 0):
        # Existing Windows gateways already permit this read-only route and
        # forward its query unchanged; a Mac UI update must not require a
        # simultaneous remote gateway deployment just to keep cards working.
        if view == "ledger":
            if offset < 0:
                raise HTTPException(400, "分页位置不能为负数。")
            return signal_ledger(source=source or "live", confirmation=confirmation or "raw", period=period,
                                 timeframe=timeframe, side=side, search=search, outcome=outcome,
                                 sort=sort, offset=offset, limit=limit)
        if view is not None:
            raise HTTPException(400, "不支持的信号视图。")
        started_ns = time.monotonic_ns()
        dispatch_trace("handler:/api/signals")
        if source not in (None, "live", "warmup", "replay", "legacy") or confirmation not in (None, "raw", "yolo", "raw_yolo"):
            raise HTTPException(400, "unsupported source or confirmation")
        source = source or "live"
        display_scope = source if source in ("live", "warmup") else None
        display_cutoff_ms = None
        if display_scope:
            # The one-time V9 arm receipt survives restarts and notification
            # setting changes. Migration timestamps can move on later cleanups.
            receipt = store.get_meta("notification_policy:v128_bark_arm", {})
            display_cutoff_ms = receipt.get("activated_ms") if isinstance(receipt, dict) else None
            if type(display_cutoff_ms) is not int or display_cutoff_ms < 0:
                raise HTTPException(503, "V12.8 正在初始化，等待首次同步完成。")
        # The V9 UI filters by confirmation.  Infer its event kind when the
        # legacy `kind` parameter is omitted, rather than silently querying
        # only YOLO rows for `confirmation=raw`.
        if kind is None:
            kind = SIGNAL_KIND if confirmation == "raw" else MODEL_KIND if confirmation == "yolo" else None
        if kind not in (None, MODEL_KIND, SIGNAL_KIND):
            raise HTTPException(400, "支持指标启动或 YOLO 确认信号。")
        direct = kind == SIGNAL_KIND
        protocol = SIGNAL_PROTOCOL if kind == SIGNAL_KIND else MODEL_PROTOCOL if kind == MODEL_KIND else (SIGNAL_PROTOCOL, MODEL_PROTOCOL)
        if source == "legacy":
            from yoyo.monitor import LEGACY_SIGNAL_KIND, LEGACY_SIGNAL_PROTOCOL, LEGACY_MODEL_PROTOCOL
            kind = LEGACY_SIGNAL_KIND if confirmation == "raw" else MODEL_KIND if confirmation == "yolo" else None
            protocol = (LEGACY_SIGNAL_PROTOCOL if confirmation == "raw" else LEGACY_MODEL_PROTOCOL
                        if confirmation == "yolo" else (LEGACY_SIGNAL_PROTOCOL, LEGACY_MODEL_PROTOCOL))
        if (before_close_ms is None) != (before_id is None):
            raise HTTPException(400, "cursor requires both close time and event id")
        event_timing = {} if DISPATCH_TRACE else None
        rows = store.list_events(limit, symbol, timeframe, kind, side, protocol=protocol,
                                 source="live" if display_scope or source == "legacy" else source, confirmation=confirmation,
                                 before_close_ms=before_close_ms, before_id=before_id,
                                 timing=event_timing, summary=True,
                                 display_scope=display_scope, display_cutoff_ms=display_cutoff_ms)
        if event_timing is not None:
            dispatch_trace("signals:sqlite", elapsed_ms=event_timing["sqlite_ms"])
            dispatch_trace("signals:decode", elapsed_ms=event_timing["decode_ms"])
        # Trace only phase names and row counts.  It deliberately excludes
        # request filters, event identities, and response content.
        dispatch_trace(f"signals:rows={len(rows)}", started_ns=started_ns)
        for row in rows:
            row["display_scope"] = source
            row["is_fresh"] = source == "live" and 0 <= monitor.client.clock() - row["bar_close_ms"] <= FRESH_MS
        dispatch_trace("signals:freshness", started_ns=started_ns)
        cursor = ({"close_ms": rows[-1]["bar_close_ms"], "event_id": rows[-1]["id"]}
                  if len(rows) == limit else None)
        dispatch_trace(f"signals:return={len(rows)}", started_ns=started_ns)
        return {"items": rows, "total": len(rows), "kind": kind, "protocol": protocol or (SIGNAL_PROTOCOL, MODEL_PROTOCOL),
                "protocols": list(protocol) if isinstance(protocol, tuple) else ([protocol] if protocol else []),
                "source": source, "confirmation": confirmation, "next_cursor": cursor,
                "display_cutoff_ms": display_cutoff_ms}

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

    @app.get("/api/shadow/status")
    def v78_shadow_status():
        try:
            return shadow_status(shadow_book)
        except ShadowBookUnavailable as error:
            if str(error) == "shadow_not_started":
                return {"configured": False, "events": 0, "v8_admitted": 0,
                        "path_bars": 0, "market_snapshots": 0, "cells": {},
                        "activation": None, "scan": None}
            raise HTTPException(503, "V7/V8 影子账本暂不可读。") from error

    @app.get("/api/shadow/events")
    def v78_shadow_events(limit: int = Query(200, ge=1, le=2000), timeframe: str = None,
                          side: int = Query(None, ge=-1, le=1)):
        if timeframe not in (None, "30m", "1H", "4H") or side == 0:
            raise HTTPException(400, "影子观察仅支持 30m、1H、4H 与多/空方向。")
        try:
            rows = shadow_events(shadow_book, limit=limit, timeframe=timeframe, side=side)
        except ShadowBookUnavailable as error:
            if str(error) == "shadow_not_started":
                rows = []
            else:
                raise HTTPException(503, "V7/V8 影子账本暂不可读。") from error
        return {"items": rows, "total": len(rows), "notification_eligible": False,
                "execution_eligible": False}

    @app.get("/api/shadow/market-state")
    def v78_shadow_market_state(limit: int = Query(12, ge=1, le=200), timeframe: str = None):
        if timeframe not in (None, "30m", "1H", "4H"):
            raise HTTPException(400, "市场状态仅支持 30m、1H、4H。")
        try:
            rows = shadow_market_snapshots(shadow_book, limit=limit, timeframe=timeframe)
        except ShadowBookUnavailable as error:
            if str(error) == "shadow_not_started":
                rows = []
            else:
                raise HTTPException(503, "V7/V8 市场状态暂不可读。") from error
        return {"items": rows, "total": len(rows), "semantics": "descriptive_only_no_gate"}

    @app.get("/api/lines/status")
    def spike_lines_status():
        try:
            result = lines_status(lines_book, monitor.client.clock())
            result["notifications"] = monitor.status().get("joint_notifications", {})
            return result
        except LinesUnavailable as error:
            if str(error) == "lines_not_started":
                return {"configured": False, "counts": {}, "activation": None, "scan": None}
            raise HTTPException(503, "趋势线突破账本暂不可读。") from error

    @app.get("/api/lines/events")
    def spike_lines_events(kind: str, timeframe: str = None, search: str = Query("", max_length=24),
                           limit: int = Query(300, ge=1, le=2000)):
        try:
            rows = lines_events(lines_book, kind=kind, now_ms=monitor.client.clock(), timeframe=timeframe,
                                search=search, limit=limit)
        except ValueError as exc:
            raise HTTPException(400, "不支持的信号类型或周期。") from exc
        except LinesUnavailable as error:
            if str(error) != "lines_not_started":
                raise HTTPException(503, "趋势线突破账本暂不可读。") from error
            rows = []
        channels = monitor.status().get("joint_notifications", {})
        enabled = kind == "joint" and any(c.get("enabled") and c.get("activated_ms") is not None
                                          for c in channels.values())
        return {"items": rows, "total": len(rows), "kind": kind, "notification_eligible": enabled,
                "execution_eligible": False}

    @app.get("/api/lines/ledger")
    def spike_lines_ledger(kind: str = "joint", period: str = "all", timeframe: str = None, scope: str = "all",
                           search: str = Query("", max_length=24), outcome: str = "all", sort: str = "newest",
                           limit: int = Query(1000, ge=1, le=2000), performance_version: str = "current"):
        try:
            return lines_ledger(lines_book, kind=kind, now_ms=monitor.client.clock(), period=period,
                                timeframe=timeframe, scope=scope, search=search, outcome=outcome, sort=sort,
                                limit=limit, performance_version=performance_version)
        except ValueError as exc:
            raise HTTPException(400, "不支持的统计筛选条件。") from exc
        except LinesUnavailable as error:
            if str(error) != "lines_not_started":
                raise HTTPException(503, "趋势线突破账本暂不可读。") from error
            return {"items": [], "total": 0, "stats": None, "by_timeframe": [], "as_of_ms": monitor.client.clock()}

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

    from yoyo.research_workspace.api import install as install_research_workspace
    install_research_workspace(app, runtime)
    from yoyo.yolo_workbench.api import install as install_yolo_workbench
    install_yolo_workbench(app, runtime, launch_worker=start_monitor)
    from yoyo.okx_execution.manual_api import install as install_manual_live
    install_manual_live(app, app.state.yolo_workbench)

    from yoyo.monitor.pro.delivery import install as install_pro
    install_pro(app)

    app.mount("/coin-assets", StaticFiles(directory=str(coin_root(STATIC)), check_dir=False), name="coin-assets")
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
