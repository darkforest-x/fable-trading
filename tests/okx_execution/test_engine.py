"""Offline exchange fault injection; no private network calls or real orders."""
from copy import deepcopy
import pytest

from yoyo.okx_execution.client import APIError, TransportError
from yoyo.okx_execution.config import Config
from yoyo.okx_execution.engine import Engine
from yoyo.okx_execution.store import Store, identifier
from yoyo.okx_execution.risk import D


def trade(identity="signal", timeframe="15m", side="long"):
    return dict(id=identity, state="prepared", symbol="ALT-USDT-SWAP", timeframe=timeframe, side=side,
                size="5", price="100", stop="98" if side == "long" else "102",
                initial_stop="98" if side == "long" else "102", tick="0.01", leverage="75",
                entry_id=identifier(identity, "entry"), algo_client_id=identifier(identity, "stop"),
                created_ms=1_000_000)


class Exchange:
    def __init__(self, t):
        self.trade, self.posts, self.fail_post = t, [], None
        self.order = dict(instId=t["symbol"], posSide=t["side"], clOrdId=t["entry_id"],
                          side="buy" if t["side"] == "long" else "sell", state="filled",
                          accFillSz="5", avgPx="100", fillTime="1000100", uTime="1000100")
        self.stop = dict(algoId="algo1", algoClOrdId=t["algo_client_id"], state="live", instId=t["symbol"],
                         tdMode="isolated",
                         posSide=t["side"], side="sell" if t["side"] == "long" else "buy", sz="5",
                         slOrdPx="-1", slTriggerPxType="last", slTriggerPx=t["stop"])
        self.positions = [dict(instId=t["symbol"], posSide=t["side"], mgnMode="isolated", pos="5", margin="1")]
        self.stop_error = None
        self.order_error = None

    def post(self, path, body):
        self.posts.append((path, deepcopy(body)))
        if self.fail_post and self.fail_post[0] == path:
            raise self.fail_post[1]
        if path.endswith("set-leverage"): return [{"lever": "75"}]
        if path.endswith("cancel-algos"): self.stop["state"] = "canceled"
        return [{"sCode": "0", "ordId": "123"}]

    def get(self, path, **query):
        if path.endswith("leverage-info"): return [{"posSide": self.trade["side"], "lever": "75"}]
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


def setup(tmp_path, t=None):
    t = t or trade()
    exchange, store = Exchange(t), Store(tmp_path)
    engine = Engine(Config(), exchange, Source(), store, clock=lambda: 1_020_000)
    return t, exchange, store, engine


def activate(t, store, engine):
    engine.enter(t)
    engine.manage(t)
    assert t["state"] == "protecting"
    engine.manage(t)
    assert t["state"] == "active"


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
    engine.enter(t)
    assert t["state"] == "skipped"
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
    exchange.positions = [dict(instId=t["symbol"], posSide=side, mgnMode="isolated", pos="20") for side in ("long", "short")]
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
