"""Retired market collectors cannot start, including via old browser requests."""
import asyncio
import pytest
from fastapi import HTTPException
from yoyo.monitor import server


def test_retired_market_workers_and_demand_routes_stay_off(tmp_path, monkeypatch):
    from yoyo.monitor.altcoin_leader_service import LeaderService
    from yoyo.monitor.vidya_retest_api import VidyaRetestService
    from yoyo.monitor.monday_fib_service import MondayFibService
    from yoyo.monitor.market_workstation import WorkstationData
    started = []
    for cls in (server.Monitor, server.MarketSync, server.MarketContext,
                LeaderService, VidyaRetestService, MondayFibService):
        monkeypatch.setattr(cls, 'start', lambda self, name=cls.__name__: started.append(name))
        monkeypatch.setattr(cls, 'close', lambda self: None)
    app = server.create_app(tmp_path)
    app.state.start_yolo_workbench = lambda: started.append('patrol')
    for name in ('monday_candles', 'market_workstation', 'signal_read_cache',
                 'okx_execution_dashboard', 'yolo_workbench_supervisor',
                 'paper_desk_worker', 'paper_desk_market'):
        obj = getattr(app.state, name)
        monkeypatch.setattr(obj, 'close', lambda: None)
        if name.startswith('paper_desk'):
            monkeypatch.setattr(obj, 'start', lambda name=name: started.append(name))
    monkeypatch.setattr(server.MarketContext, 'snapshot', lambda *a, **k: pytest.fail('context collected'))
    monkeypatch.setattr(WorkstationData, 'snapshot', lambda *a, **k: pytest.fail('workstation collected'))
    async def run():
        async with app.router.lifespan_context(app):
            routes = {r.path:r.endpoint for r in app.routes if hasattr(r, 'endpoint')}
            for path in ('/api/market-sync/context', '/api/market-workstation'):
                with pytest.raises(HTTPException) as error:
                    routes[path]()
                assert error.value.status_code == 410
            status = routes['/api/market-sync']()
            assert status['monitoring_enabled'] is False
            assert not any(status['workers'].values())
    asyncio.run(run())
    assert set(started) == {'Monitor', 'LeaderService', 'VidyaRetestService',
                            'MondayFibService', 'patrol', 'paper_desk_worker', 'paper_desk_market'}
