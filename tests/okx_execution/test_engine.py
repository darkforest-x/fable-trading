"""Offline exchange fault injection; no private network calls or real orders."""
from copy import deepcopy
import pytest

from yoyo.okx_execution.client import APIError, TransportError
from yoyo.okx_execution.config import Config
from yoyo.okx_execution.engine import Engine
from yoyo.okx_execution.store import Store, identifier
from yoyo.okx_execution.risk import D
from yoyo.okx_execution.preparation import RetryableRead
from yoyo.monitor import FRESH_MS


def trade(identity="signal", timeframe="15m", side="long"):
    return dict(id=identity, state="prepared", symbol="ALT-USDT-SWAP", timeframe=timeframe, side=side,
                size="5", price="100", stop="98" if side == "long" else "102",
                initial_stop="98" if side == "long" else "102", tick="0.01", leverage="75",
                entry_id=identifier(identity, "entry"), algo_client_id=identifier(identity, "stop"),
                created_ms=1_000_000, margin="1",
                event=dict(id=identity, symbol="ALT-USDT-SWAP", timeframe=timeframe, side=side,
                           bar_close_ms=1_000_000, initial_stop=98 if side == "long" else 102))


class Exchange:
    def __init__(self, t):
        self.trade, self.posts, self.fail_post = t, [], None
        self.order = dict(instId=t["symbol"], posSide=t["side"], clOrdId=t["entry_id"],
                          side="buy" if t["side"] == "long" else "sell", state="filled",
                          accFillSz="5", avgPx="100", fillTime="1000100", uTime="1000100")
        self.stop = dict(algoId="algo1", algoClOrdId=t["algo_client_id"], state="live", instId=t["symbol"],
                         tdMode="cross",
                         posSide=t["side"], side="sell" if t["side"] == "long" else "buy", sz="5",
                         slOrdPx="-1", slTriggerPxType="last", slTriggerPx=t["stop"])
        self.positions = []
        self.stop_error = None
        self.order_error = None
        self.quote = dict(askPx="100", bidPx="100", ts="1020000")

    def post(self, path, body):
        self.posts.append((path, deepcopy(body)))
        if self.fail_post and self.fail_post[0] == path:
            raise self.fail_post[1]
        if path.endswith("/order") and body.get("clOrdId") == self.trade["entry_id"]:
            self.positions = [dict(instId=self.trade["symbol"], posSide=self.trade["side"], mgnMode="cross", pos=self.trade["size"])]
        if path.endswith("set-leverage"): return [{"lever": "75"}]
        if path.endswith("cancel-algos"): self.stop["state"] = "canceled"
        return [{"sCode": "0", "ordId": "123"}]

    def get(self, path, **query):
        if path.endswith('/ticker'): return [deepcopy(self.quote)]
        if path.endswith('/instruments'):
            return [dict(instId=self.trade['symbol'], instFamily='ALT-USDT', instType='SWAP', ctType='linear',
                         settleCcy='USDT', state='live', ctVal='.25', lotSz='1', minSz='1',
                         maxMktSz='10000', tickSz='.01', lever='100')]
        if path.endswith('/position-tiers'): return [dict(minSz='0',maxSz='10000',maxLever='75')]
        if path.endswith("leverage-info"): return [{"instId": self.trade["symbol"], "mgnMode": "cross", "posSide": "", "lever": "75"}]
        if path.endswith("/config"): return [dict(acctLv="2",posMode="long_short_mode",uid="test")]
        if path.endswith("/balance"): return [dict(details=[dict(ccy="USDT",eq="125",availEq="125",imr="0")])]
        if path.endswith("order-algo"):
            if self.stop_error: raise self.stop_error
            return [deepcopy(self.stop)]
        if path.endswith("/order"):
            if self.order_error: raise self.order_error
            return [deepcopy(self.order)]
        if path.endswith("positions"): return deepcopy(self.positions)
        if path.endswith("orders-pending") or path.endswith("orders-algo-pending"): return []
        raise AssertionError((path, query))


class Source:
    rows = []
    def bars(self, *args): return deepcopy(self.rows)
    def checkpoint(self, *args): return {}
    def events(self, *args): return []


def setup(tmp_path, t=None):
    t = t or trade()
    exchange, store = Exchange(t), Store(tmp_path)
    engine = Engine(Config(), exchange, Source(), store, clock=lambda: 1_020_000)
    return t, exchange, store, engine


def test_excluded_new_entry_never_reaches_leverage_or_order_api(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    t['symbol'] = 'ICP-USDT-SWAP'
    with pytest.raises(ValueError, match='excluded'):
        engine.prepare({'symbol': t['symbol']}, {})
    engine.enter(t)
    assert exchange.posts == [] and store.trades()[0]['state'] == 'skipped'


def test_excluded_existing_position_still_reaches_management(tmp_path, monkeypatch):
    t, exchange, store, engine = setup(tmp_path)
    t.update(symbol='ICP-USDT-SWAP', state='active')
    store.save(t)
    managed = []
    monkeypatch.setattr(engine, 'manage', lambda row: managed.append(row['symbol']))
    engine.tick(entries=False)
    assert managed == ['ICP-USDT-SWAP'] and exchange.posts == []


def activate(t, store, engine):
    engine.enter(t)
    engine.manage(t)
    assert t["state"] == "protecting"
    engine.manage(t)
    assert t["state"] == "active"


def test_verified_unchanged_four_hour_position_does_not_block_new_signal(tmp_path, monkeypatch):
    from yoyo.monitor import store as monitor_store
    from yoyo.okx_execution.source import Source as MonitorSource

    closed = 144_000_000
    now = closed + 3 * 3_600_000
    t, exchange, store, engine = setup(tmp_path, trade(timeframe="4H"))
    t.update(state="active", last_bar_ms=closed, algo_id="algo1")
    store.save(t)
    store.set_meta("cutoff_ms", 0)
    exchange.positions = [dict(instId=t["symbol"], posSide=t["side"], mgnMode="cross", pos=t["size"])]
    engine.clock = lambda: now
    source_dir = tmp_path / "monitor"
    seed = monitor_store.Store(source_dir / "monitor.sqlite3")
    monkeypatch.setattr(monitor_store, "now_ms", lambda: closed + 1_000)
    seed.save_candle_checkpoint(t["symbol"], "4H", [
        dict(t=closed - 14_400_000, o=100., h=101., l=99., c=100., v=10.)])
    seed.upsert_market(dict(symbol=t["symbol"], timeframe="4H", tick_size="0.01"))
    engine.source = MonitorSource(source_dir)
    fresh = dict(id="new-mina", symbol="MINA-USDT-SWAP", timeframe="30m", side="long", bar_close_ms=now)
    monkeypatch.setattr(engine.source, "events", lambda *args: [fresh])
    entered = []
    monkeypatch.setattr(engine, "prepare", lambda event, account: event)
    monkeypatch.setattr(engine, "enter", lambda row: entered.append(row["id"]))
    engine.tick()
    assert not entered and store.meta("errors")[0]["error"] == "monitor checkpoint unavailable or stale"
    monkeypatch.setattr(monitor_store, "now_ms", lambda: now)
    seed.observe_candle_checkpoint(t["symbol"], "4H", closed)
    engine.tick()
    assert entered == ["new-mina"] and store.meta("errors") == []
    assert exchange.posts == []


def test_transport_timeout_is_durable_and_never_reposted_after_restart(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    exchange.fail_post = ("/api/v5/trade/order", TransportError("timeout"))
    with pytest.raises(TransportError): engine.enter(t)
    recovered = Store(tmp_path).trades()[0]
    assert recovered["state"] == "entry_pending"
    exchange.order_error = APIError("51603")
    with pytest.raises(APIError): engine.manage(recovered)
    assert len([p for p in exchange.posts if p[0].endswith("/order")]) == 1
    exchange.order_error = None
    engine.manage(recovered)
    engine.manage(recovered)
    assert recovered["state"] == "active"
    assert len([p for p in exchange.posts if p[0].endswith("/order")]) == 1


def test_definitive_entry_rejection_remains_rejected(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    exchange.fail_post = ("/api/v5/trade/order", APIError("51008"))
    engine.enter(t)
    assert store.trades()[0]["state"] == "rejected"


def test_quote_expiry_during_setup_cannot_send_an_order(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    t["quote_ms"] = 1_000_000
    exchange.quote['ts'] = '1000000'
    engine.enter(t)
    assert store.trades()[0]["state"] == "retry_pending"
    assert not [p for p in exchange.posts if p[0].endswith("/order")]


def test_no_protection_is_not_reported_active(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    engine.enter(t); engine.manage(t)
    exchange.stop["state"] = "order_failed"
    engine.manage(t)
    assert t["state"] == "exit_needed"
    engine.manage(t)
    assert t["state"] == "exit_pending"
    request = exchange.posts[-1][1]
    assert request["side"] == "sell" and request["posSide"] == "long"
    assert request["sz"] == "5" and "reduceOnly" not in request


def test_partial_entry_cancel_then_close_only_actual_fill(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    engine.enter(t)
    exchange.order.update(state="partially_filled", accFillSz="2")
    engine.clock = lambda: 1_031_000  # Timeout starts at the actual entry attempt.
    engine.manage(t)
    assert t["state"] == "cancel_entry_pending"
    assert exchange.posts[-1][0].endswith("cancel-order")
    exchange.order["state"] = "canceled"
    engine.manage(t)
    assert t["state"] == "exit_needed" and t["size"] == "2"


def test_trail_requires_exchange_readback_and_is_not_an_inferred_fill(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    activate(t, store, engine)
    engine.source.rows = [dict(close=105, atr=1, raw_side=0, close_ms=1_010_000)]
    engine.manage(t)
    assert t["state"] == "amend_pending" and t["stop"] == "98"
    assert exchange.posts[-1][1]["cxlOnFail"] is False
    with pytest.raises(ValueError, match="unresolved"): engine.manage(t)
    exchange.stop["slTriggerPx"] = "101"
    engine.manage(t)
    assert t["state"] == "active" and D(t["stop"]) == 101


def test_raw_opposite_cancels_only_own_stop_then_closes_own_lot(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    activate(t, store, engine)
    engine.source.rows = [dict(close=101, atr=1, raw_side=-1, close_ms=1_010_000)]
    engine.manage(t)
    assert t["state"] == "exit_needed"
    engine.manage(t)
    assert t["state"] == "cancel_stop_pending"
    assert exchange.posts[-1][1] == [dict(instId=t["symbol"], algoId="algo1")]
    engine.manage(t)
    assert t["state"] == "exit_pending"
    exchange.order.update(side="sell", clOrdId=t["exit_id"], avgPx="101")
    engine.manage(t)
    assert t["state"] == "closed" and t["exit_reason"] == "raw_opposite"


def test_stop_filling_during_cancel_is_reconciled_without_second_close(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    activate(t, store, engine)
    t["state"] = "cancel_stop_pending"; store.save(t)
    exchange.stop.update(state="effective", ordId="stop-order")
    exchange.positions = []
    engine.manage(t)
    assert t["state"] == "stop_executing"
    exchange.order.update(side="sell", avgPx="98")
    engine.manage(t)
    assert t["state"] == "closed"
    assert len([p for p in exchange.posts if p[0].endswith("/order")]) == 1


def test_manual_position_change_prevents_closing_someone_elses_quantity(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    activate(t, store, engine)
    t["state"] = "exit_needed"; store.save(t)
    exchange.positions[0]["pos"] = "6"
    before = len(exchange.posts)
    with pytest.raises(ValueError, match="ownership changed"): engine.manage(t)
    assert len(exchange.posts) == before


def test_more_than_three_concurrent_lots_and_both_sides_are_supported(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    for i in range(8):
        row = trade(str(i), ["15m", "30m", "1H", "4H"][i % 4], "long" if i < 4 else "short")
        row.update(state="active", algo_id=f"algo{i}")
        store.save(row)
    exchange.positions = [dict(instId=t["symbol"], posSide=side, mgnMode="cross", pos="20") for side in ("long", "short")]
    assert len(engine.owned(dict(positions=exchange.positions))) == 2
    engine.assert_owned_quantity()


def test_exchange_failure_is_not_flat_and_does_not_send_orders(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    activate(t, store, engine)
    exchange.stop_error = TransportError("unavailable")
    before = len(exchange.posts)
    engine.tick()
    assert store.meta("errors") and store.trades()[0]["state"] == "active"
    assert len(exchange.posts) == before


def test_cross_shared_leverage_entry_stop_and_exit_contract(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    activate(t, store, engine)
    leverage = exchange.posts[0]
    assert leverage == ("/api/v5/account/set-leverage", dict(instId=t["symbol"], mgnMode="cross", lever="75"))
    entry = next(body for path, body in exchange.posts if path.endswith('/order'))
    assert entry['tdMode'] == 'cross' and entry['posSide'] == 'long'
    wrong_stop = dict(exchange.stop, tdMode='isolated')
    with pytest.raises(ValueError):
        engine.verify_stop(t, wrong_stop)
    engine.exit(t)
    assert t["state"] == "cancel_stop_pending"
    engine.exit(t)  # Confirm cancellation before sending the owned close.
    exits = [body for path, body in exchange.posts if path.endswith('/order') and body.get('clOrdId') != t['entry_id']]
    assert exits and exits[-1]['tdMode'] == 'cross'


def test_cross_uses_available_equity_and_currency_imr(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    get = exchange.get
    exchange.get = lambda path, **query: ([{'details':[dict(ccy='USDT',eq='125',availBal='999',availEq='1',imr='124')]}]
        if path.endswith('/balance') else get(path, **query))
    account = engine.account()
    assert account['available'] == '1' and account['allocated_margin'] == '124'
    # Available collateral must be checked again after the shared leverage POST.
    t['margin'] = '4'
    engine.enter(t)
    assert store.trades()[0]['state'] == 'skipped'
    assert len(exchange.posts) == 1 and exchange.posts[0][0].endswith('set-leverage')


def test_quote_generated_during_request_uses_receipt_clock(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    now = [1_020_000]
    engine.clock = lambda: now[0]
    original = exchange.get
    def get(path, **query):
        now[0] += 400
        if path.endswith('/ticker'):
            exchange.quote['ts'] = str(now[0] - 50)
        return original(path, **query)
    exchange.get = get
    prepared = engine.prepare(t['event'], dict(equity='125',available='125',allocated_margin='0',positions=[]))
    assert prepared['quote_ms'] > prepared['quote_request_ms']
    assert prepared['quote_age_ms'] == 50
    assert prepared['quote_received_ms'] == now[0]
    assert exchange.posts == []


@pytest.mark.parametrize('offset', [-5001, 5001])
def test_stale_or_future_quote_is_still_rejected(tmp_path, offset):
    t, exchange, store, engine = setup(tmp_path)
    exchange.quote['ts'] = str(engine.clock() + offset)
    with pytest.raises(RetryableRead) as exc:
        engine.prepare(t['event'], {})
    assert exc.value.code == 'quote_stale'
    assert exc.value.evidence['quote_age_ms'] == -offset
    assert exchange.posts == []


@pytest.mark.parametrize('ahead', [1, 1192, 1764, 5000])
def test_signal_clock_lead_waits_and_preserves_receipt_evidence(tmp_path, monkeypatch, ahead):
    t, exchange, store, engine = setup(tmp_path)
    now = [engine.clock()]
    engine.clock = lambda: now[0]
    received = now[0]
    exchange.quote['ts'] = str(received + ahead)
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        now[0] += round(seconds * 1000)
    monkeypatch.setattr('yoyo.okx_execution.engine.time.sleep', sleep)
    prepared = engine.prepare(t['event'], dict(equity='125', available='125', allocated_margin='0', positions=[]))
    assert waits == [(ahead + 1) / 1000]
    assert prepared['quote_received_ms'] == received
    assert prepared['quote_ms'] == received + ahead
    assert prepared['quote_received_age_ms'] == -ahead
    assert prepared['quote_validated_ms'] == now[0]
    assert prepared['quote_age_ms'] == 1
    assert prepared['quote_wait_ms'] == ahead + 1
    assert exchange.posts == []


@pytest.mark.parametrize('advance_ms', [0, 7000])
def test_signal_clock_wait_still_rejects_future_or_old_quote(tmp_path, monkeypatch, advance_ms):
    t, exchange, store, engine = setup(tmp_path)
    now = [engine.clock()]
    engine.clock = lambda: now[0]
    exchange.quote['ts'] = str(now[0] + 1192)
    def sleep(seconds):
        now[0] += advance_ms
    monkeypatch.setattr('yoyo.okx_execution.engine.time.sleep', sleep)
    with pytest.raises(RetryableRead):
        engine.prepare(t['event'], {})
    assert exchange.posts == []


def test_signal_expiring_during_clock_wait_never_reaches_leverage(tmp_path, monkeypatch):
    t, exchange, store, engine = setup(tmp_path)
    now = [t['event']['bar_close_ms'] + FRESH_MS - 1000]
    engine.clock = lambda: now[0]
    engine.source.events = lambda *args: [deepcopy(t['event'])]
    exchange.quote['ts'] = str(now[0] + 1192)
    def sleep(seconds):
        now[0] += round(seconds * 1000)
    monkeypatch.setattr('yoyo.okx_execution.engine.time.sleep', sleep)
    engine.tick()
    assert store.trades()[0]['state'] == 'skipped'
    assert store.trades()[0]['reason_code'] == 'signal_expired'
    assert exchange.posts == []


@pytest.mark.parametrize('side', ['long', 'short'])
def test_clock_lead_on_both_preparations_enters_market_once(tmp_path, monkeypatch, side):
    t, exchange, store, engine = setup(tmp_path, trade(side=side))
    now = [engine.clock()]
    engine.clock = lambda: now[0]
    engine.source.events = lambda *args: [deepcopy(t['event'])]
    original = exchange.get
    def get(path, **query):
        if path.endswith('/ticker'):
            exchange.quote['ts'] = str(now[0] + 1764)
        return original(path, **query)
    exchange.get = get
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        now[0] += round(seconds * 1000)
    monkeypatch.setattr('yoyo.okx_execution.engine.time.sleep', sleep)
    engine.tick()
    assert len(waits) == 2  # Initial sizing and post-leverage sizing.
    request = next(body for path, body in exchange.posts if path.endswith('/order'))
    assert request['ordType'] == 'market' and 'px' not in request
    assert request['side'] == ('buy' if side == 'long' else 'sell')
    assert request['attachAlgoOrds'][0]['slOrdPx'] == '-1'
    engine.manage(store.trades()[0])
    engine.manage(store.trades()[0])
    engine.tick()
    assert store.trades()[0]['state'] == 'active'
    assert len([p for p in exchange.posts if p[0].endswith('/order')]) == 1


def test_slow_leverage_setup_refreshes_quote_and_risk_sized_quantity(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    t['quote_ms'] = 1_000_000  # Initial quote is already > 5 seconds old.
    exchange.quote.update(askPx='102', bidPx='102')
    engine.enter(t)
    request = next(body for path,body in exchange.posts if path.endswith('/order'))
    assert request['sz'] == '2'  # 2.5 / ((102 - 98) * .25), rounded down.
    assert D(t['planned_risk']) <= D('2.5')
    assert t['quote_ms'] == engine.clock() and t['state'] == 'entry_pending'


def test_retry_survives_restart_and_backoff_then_enters_once(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    engine.source.events = lambda *args: [deepcopy(t['event'])]
    exchange.quote['ts'] = '1000000'
    engine.tick()
    waiting = store.trades()[0]
    assert waiting['state'] == 'retry_pending' and waiting['attempts'] == 1
    assert waiting['symbol'] == t['symbol'] and waiting['quote_age_ms'] == 20000
    assert exchange.posts == []
    engine.tick()  # Backoff prevents consuming this event on every loop.
    assert store.trades()[0] == waiting
    clock = waiting['next_retry_ms']
    engine = Engine(Config(), exchange, engine.source, Store(tmp_path), clock=lambda: clock)
    exchange.quote['ts'] = str(clock)
    engine.tick()
    assert store.trades()[0]['state'] == 'entry_pending'
    with pytest.raises(ValueError, match='unresolved execution intent'):
        engine.tick()  # Fill found; protection still needs readback before entries.
    engine.tick()
    assert len([p for p in exchange.posts if p[0].endswith('/order')]) == 1


@pytest.mark.parametrize('failure', [TransportError('GET unavailable'), APIError('50011'),
                                    RetryableRead('monitor checkpoint unavailable or stale','checkpoint_unavailable')])
def test_transient_pre_entry_reads_wait_instead_of_consuming_signal(tmp_path, failure, monkeypatch):
    t, exchange, store, engine = setup(tmp_path)
    engine.source.events = lambda *args: [t['event']]
    def fail(*args): raise failure
    monkeypatch.setattr(engine, 'prepare', fail)
    engine.tick()
    assert store.trades()[0]['state'] == 'retry_pending'
    assert exchange.posts == []
    engine.clock = lambda: t['event']['bar_close_ms'] + FRESH_MS + 1
    engine.tick()
    assert store.trades()[0]['state'] == 'skipped'
    assert store.trades()[0]['reason_code'] == 'signal_expired'
    assert exchange.posts == []


@pytest.mark.parametrize('failure', [ValueError('risk-sized quantity is below exchange minimum'), APIError('50113')])
def test_deterministic_rejection_is_not_retried(tmp_path, failure, monkeypatch):
    t, exchange, store, engine = setup(tmp_path)
    engine.source.events = lambda *args: [t['event']]
    def fail(*args): raise failure
    monkeypatch.setattr(engine, 'prepare', fail)
    engine.tick()
    initial = store.trades()[0]
    assert initial['state'] == 'skipped'
    engine.tick()
    assert store.trades()[0] == initial and exchange.posts == []


def test_old_skipped_signal_is_never_reopened(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    old = dict(id=t['id'], event=t['event'], state='skipped', reason='exchange quote is stale')
    store.save(old)
    engine.source.events = lambda *args: [t['event']]
    engine.tick()
    assert store.trades() == [old] and exchange.posts == []


def test_retry_record_with_entry_intent_blocks_new_orders(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    store.save(dict(t, state='retry_pending', entry_request={'clOrdId':t['entry_id']}, next_retry_ms=0))
    engine.tick()
    assert store.meta('errors') and not store.meta('accepting_entries')
    assert exchange.posts == []


def test_leverage_timeout_remains_uncertain_and_is_not_reposted(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    exchange.fail_post = ('/api/v5/account/set-leverage', TransportError('POST timeout'))
    with pytest.raises(TransportError): engine.enter(t)
    assert store.trades()[0]['state'] == 'leverage_pending'
    engine.manage(store.trades()[0])
    assert len(exchange.posts) == 1 and store.trades()[0]['state'] == 'skipped'


def test_tier_change_after_leverage_waits_without_entry(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    original = exchange.get
    exchange.get = lambda path, **kw: ([dict(minSz='0',maxSz='10000',maxLever='25')]
        if path.endswith('/position-tiers') else original(path, **kw))
    engine.enter(t)
    assert store.trades()[0]['state'] == 'retry_pending'
    assert store.trades()[0]['reason_code'] == 'tier_changed'
    assert len(exchange.posts) == 1


def test_signal_expiry_during_slow_setup_cannot_refresh_eligibility(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    engine.clock = lambda: t['event']['bar_close_ms'] + FRESH_MS + 1
    engine.enter(t)
    assert store.trades()[0]['reason_code'] == 'signal_expired'
    assert not [p for p in exchange.posts if p[0].endswith('/order')]


def test_confirmed_leverage_then_get_failure_is_retryable_without_entry(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    original = exchange.get
    def get(path, **kw):
        if path.endswith('/balance'): raise TransportError('GET unavailable')
        return original(path, **kw)
    exchange.get = get
    engine.enter(t)
    assert store.trades()[0]['state'] == 'retry_pending'
    assert len(exchange.posts) == 1 and exchange.posts[0][0].endswith('set-leverage')


def test_waiting_lots_do_not_change_owned_position_totals_or_manage_only(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    engine.preparation_failed(t['event'], RetryableRead('exchange quote is stale','quote_stale'))
    assert engine.owned(dict(positions=[])) == {}
    engine.assert_owned_quantity([])
    engine.source.events = lambda *args: (_ for _ in ()).throw(AssertionError('manage-only cannot enter'))
    engine.tick(entries=False)
    assert store.trades()[0]['state'] == 'retry_pending'
    assert store.meta('entries_enabled') is False and exchange.posts == []


def test_slow_intent_persistence_cannot_send_expired_quote(tmp_path, monkeypatch):
    t, exchange, store, engine = setup(tmp_path)
    clock = [engine.clock()]
    engine.clock = lambda: clock[0]
    save = store.save
    def slow_save(row):
        save(row)
        if row['state'] == 'entry_pending': clock[0] += 5001
    monkeypatch.setattr(store,'save',slow_save)
    engine.enter(t)
    assert store.trades()[0]['state'] == 'skipped'
    assert not [p for p in exchange.posts if p[0].endswith('/order')]


def test_excluded_waiting_signal_becomes_terminal_without_new_entry(tmp_path):
    t, exchange, store, engine = setup(tmp_path)
    event = dict(t['event'],symbol='ICP-USDT-SWAP')
    engine.preparation_failed(event, RetryableRead('exchange quote is stale','quote_stale'))
    engine.tick()
    assert store.trades()[0]['state'] == 'skipped' and exchange.posts == []
