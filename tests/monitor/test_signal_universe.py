"""Owner exclusions block new signals without orphaning reference or owned exits."""
import json
import sqlite3

import pytest

from yoyo.contracts.signal_universe import EXCLUDED_BASES, exclusion_reason, instrument_exclusion
from yoyo.monitor import signal_universe as universe
from yoyo.monitor.store import Store
from yoyo.monitor.v130_policy import SIGNAL_KIND, SIGNAL_PROTOCOL


def instrument(base, settlement="USDT"):
    return dict(instId=f"{base}-{settlement}-SWAP", instType="SWAP", state="live",
                ctType="linear" if settlement == "USDT" else "inverse", settleCcy=settlement, tickSz=".01")


@pytest.mark.parametrize("base", EXCLUDED_BASES)
def test_exact_owner_assets_excluded(base):
    assert exclusion_reason(f"{base}-USDT-SWAP") == "owner_excluded_asset"
    assert exclusion_reason(f"{base}X-USDT-SWAP") is None


def test_only_explicit_live_linear_usdt_instruments_admitted():
    assert instrument_exclusion(instrument("DOGE")) is None
    assert instrument_exclusion(instrument("BTC", "USD"))
    for field, value in [("ctType", "inverse"), ("state", "preopen"), ("settleCcy", "USDC"), ("instType", "FUTURES")]:
        assert instrument_exclusion(dict(instrument("ETH"), **{field: value}))
    assert instrument_exclusion({"instId": "ETH-USDT-SWAP"})


def reference(store, symbol):
    from test_v130_signals import fixture
    from yoyo.monitor.v130_signals import replay
    from yoyo.monitor.v130_policy import arm_v130
    built, side, anchors = fixture([(11.2, 11.4, 10.9, 11.3)])
    event = dict(replay(built, side, anchors, tick=.1)[0][0], symbol=symbol)
    event['detected_at_ms'] = event['bar_close_ms'] + 1
    arm_v130(store, event['bar_close_ms'] - 1)
    assert event['performance']['status'] == 'active'
    assert store.upsert_event(event)
    store.upsert_market(dict(symbol=symbol, timeframe="15m", active=True, chart=[{"c": 42}], events=[event]))


def test_retired_symbols_keep_management_data_but_leave_active_market_pool(tmp_path, monkeypatch):
    store = Store(tmp_path / "monitor.sqlite3")
    reference(store, "GMT-USDT-SWAP")
    monkeypatch.setattr(universe, "execution_symbols", lambda _: {"ICP-USDT-SWAP"})
    inputs = [instrument(x) for x in ["ETH", "GMT", "ICP", "HOT"]] + [instrument("BTC", "USD")]
    selected, eligible = universe.plan_scan(store, inputs, 1000)
    assert eligible == {"ETH-USDT-SWAP"}
    assert {r['instId'] for r in selected} == eligible | {"GMT-USDT-SWAP", "ICP-USDT-SWAP"}
    old = store.get_market("GMT-USDT-SWAP", "15m")
    assert old['active'] is False and old['chart'] == [{"c": 42}] and len(old['events']) == 1
    assert store.event_count() == 1
    meta = store.get_meta("universe")
    assert (meta['count'], meta['excluded_count'], meta['maintenance_count']) == (1, 4, 2)
    # Once both reference and owned positions have resolved, no further scan is needed.
    with store.connect() as db:
        db.execute("UPDATE events SET payload=json_set(payload,'$.performance.status','profit')")
    monkeypatch.setattr(universe, "execution_symbols", lambda _: set())
    assert len(universe.plan_scan(store, inputs, 2000)[0]) == 1


def test_unknown_ownership_retains_candles_without_allowing_entries(tmp_path, monkeypatch):
    store = Store(tmp_path / "monitor.sqlite3")
    monkeypatch.setattr(universe, "execution_symbols", lambda _: None)
    selected, eligible = universe.plan_scan(store, [instrument('ETH'), instrument('GMT')], 1000)
    assert len(selected) == 2 and eligible == {'ETH-USDT-SWAP'}
    assert store.get_meta('universe')['management_ownership_known'] is False


def test_read_only_owned_ledger_uses_source_binding_and_keeps_unresolved_intents(tmp_path, monkeypatch):
    monitor, execution = tmp_path / 'monitor', tmp_path / 'execution'
    monitor.mkdir(); execution.mkdir()
    monkeypatch.setattr(universe, 'MONITOR_RUNTIME', monitor)
    monkeypatch.setattr(universe, 'EXECUTION_RUNTIME', execution)
    (execution / 'config.json').write_text(json.dumps({'source': str(monitor)}))
    path = execution / 'execution.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE trades(value TEXT)')
        db.executemany('INSERT INTO trades VALUES (?)', [(json.dumps(r),) for r in [
            {'state':'entry_pending','event':{'symbol':'ICP-USDT-SWAP'}},
            {'state':'closed','symbol':'GMT-USDT-SWAP'}]])
    before = path.read_bytes()
    assert universe.execution_symbols(monitor) == {'ICP-USDT-SWAP'}
    assert path.read_bytes() == before
    (execution / 'config.json').write_text(json.dumps({'source': str(tmp_path / 'other')}))
    assert universe.execution_symbols(monitor) == set()


def test_production_scanner_fetches_only_admitted_or_management_symbols(tmp_path, monkeypatch):
    from yoyo.monitor import v9_worker, TIMEFRAMES
    store = Store(tmp_path / 'monitor.sqlite3')
    store.reset_for_v9(0)
    reference(store, 'GMT-USDT-SWAP')
    monkeypatch.setattr(universe, 'execution_symbols', lambda _: set())
    calls = []

    class Client:
        def synchronize(self): pass
        def clock(self): return 1_900_000_000_000
        def instruments(self):
            return [instrument('ETH'), instrument('GMT'), instrument('ICP'), instrument('BTC', 'USD')]
        def candles(self, symbol, timeframe, previous=None, limit=720):
            calls.append((symbol, timeframe))
            step = TIMEFRAMES[timeframe]
            return [dict(t=self.clock() // step * step - step, o=100., h=101., l=99., c=100., v=1.)], 0

    monkeypatch.setattr(v9_worker, 'OKX', lambda **kwargs: Client())
    monkeypatch.setattr(v9_worker, 'analyze', lambda candles, higher, timeframe, **kw:
        dict(state=dict(phase='ready', timeframe=timeframe), chart=candles, events=[]))
    v9_worker.V9Scanner(str(store.path)).scan_once()
    assert set(calls) == {(s, tf) for s in ['ETH-USDT-SWAP', 'GMT-USDT-SWAP'] for tf in TIMEFRAMES}
    assert store.get_meta('universe')['count'] == 1
    assert store.get_meta('scan')['errors'] == 0
    assert store.get_market('GMT-USDT-SWAP', '15m')['active'] is False
    assert store.get_market('ETH-USDT-SWAP', '15m')['active'] is True
