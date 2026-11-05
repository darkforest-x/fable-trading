"""Owner 2026-11-06 performance pass: retired scanners and cached symbol list.

The V12.8 scanner, its YOLO confirmation worker, the trendline/joint scanner
and the paper desk background feed no longer start. Status must say so rather
than present the last receipt as a live scan. The workbench symbol catalogue is
read from OKX at most once per five minutes.
"""
import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from yoyo.monitor import server, service
from yoyo.monitor.service import Monitor, RETIRED_WORKERS
from yoyo.monitor.store import Store


class FakeMarket:
    """Clock-only client; the retired workers must never touch market data."""
    offset_ms = 0
    requests = 0

    def clock(self):
        return 1_793_900_000_000


def test_monitor_start_skips_retired_workers_and_reports_retired_scan(tmp_path, monkeypatch):
    store = Store(tmp_path / "monitor.sqlite3")
    store.set_meta("migration:spike-v9-reset-v1", {"activated_ms": 1})
    store.set_meta("scan", {"status": "idle", "finished_at_ms": 1, "completed": 9, "total": 9, "errors": 0})
    starts = []

    class InertThread:
        def __init__(self, *, name, **kwargs):
            self.name = name
        def start(self):
            starts.append(self.name)
        def join(self, timeout=None):
            pass

    monkeypatch.setattr(service.threading, "Thread", InertThread)
    monitor = Monitor(store, client=FakeMarket())
    monitor.start()
    assert set(RETIRED_WORKERS) == {"scan", "model", "lines"}
    assert set(starts) == {"impulse-telegram", "impulse-bark", "impulse-status"}
    assert monitor.scan_process is None and monitor.model_process is None and monitor.lines_process is None
    scan = store.get_meta("scan")
    assert scan["status"] == "retired" and scan["reason"] == RETIRED_WORKERS["scan"]
    health = monitor.health()
    assert health["scan"]["status"] == "retired" and health["market_ready"] is False
    monitor.close()


def test_paper_desk_background_feed_no_longer_starts(tmp_path, monkeypatch):
    from yoyo.monitor.altcoin_leader_service import LeaderService
    from yoyo.monitor.vidya_retest_api import VidyaRetestService
    from yoyo.monitor.monday_fib_service import MondayFibService
    started = []
    for cls in (server.Monitor, server.MarketSync, server.MarketContext,
                LeaderService, VidyaRetestService, MondayFibService):
        monkeypatch.setattr(cls, 'start', lambda self, name=cls.__name__: started.append(name))
        monkeypatch.setattr(cls, 'close', lambda self: None)
    app = server.create_app(tmp_path)
    app.state.start_yolo_workbench = lambda: started.append('patrol')
    for name in ('paper_desk_worker', 'paper_desk_market'):
        monkeypatch.setattr(getattr(app.state, name), 'start', lambda name=name: started.append(name))

    async def run():
        async with app.router.lifespan_context(app):
            pass
    asyncio.run(run())
    assert 'paper_desk_worker' not in started and 'paper_desk_market' not in started
    # Quotes stay on demand for page reads.
    assert app.state.paper_desk_quotes.background is False


def test_workbench_symbols_read_okx_once_per_cache_window(tmp_path, monkeypatch):
    from yoyo.yolo_workbench import api as workbench_api
    from yoyo.yolo_workbench.api import install
    from yoyo.yolo_workbench.market import Market
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(Market, 'universe', lambda self: calls.append(1) or [dict(symbol='ETH-USDT-SWAP', name='ETH', listed_ms=1)])
    monkeypatch.setattr(workbench_api.time, 'monotonic', lambda: clock[0])
    app = FastAPI(); install(app, tmp_path, launch_worker=False); client = TestClient(app)
    first = client.get('/api/yolo-detect/symbols').json()
    second = client.get('/api/yolo-detect/symbols').json()
    assert len(calls) == 1 and first == second and first['items'][0]['symbol'] == 'ETH-USDT-SWAP'
    clock[0] += workbench_api.SYMBOL_CACHE_SECONDS
    client.get('/api/yolo-detect/symbols')
    assert len(calls) == 2


def test_workbench_symbols_failure_is_not_cached(tmp_path, monkeypatch):
    from yoyo.yolo_workbench.api import install
    from yoyo.yolo_workbench.market import Market
    outcomes = [RuntimeError('down'), [dict(symbol='BTC-USDT-SWAP', name='BTC', listed_ms=1)]]

    def universe(self):
        value = outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(Market, 'universe', universe)
    app = FastAPI(); install(app, tmp_path, launch_worker=False); client = TestClient(app)
    assert client.get('/api/yolo-detect/symbols').status_code == 503
    assert client.get('/api/yolo-detect/symbols').json()['items'][0]['symbol'] == 'BTC-USDT-SWAP'
