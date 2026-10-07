import tempfile
from pathlib import Path

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.okx.client import OkxClient
from yoyo.copier.okx.executor import (
    OKX_CHANNEL_NOTIONAL_LEVERAGE_MAP_KEY,
    OKX_MARGIN_BUFFER_PCT,
    OkxExecutor,
)
from yoyo.copier.risk.engine import RiskEngine
from yoyo.copier.store.sqlite import Database


class FakeOkxClient:
    def __init__(self):
        self.leverage_calls = []
        self.orders = []
        self.algos = []

    def get_ticker(self, inst_id):
        return 100.0

    def get_instrument(self, inst_id):
        return {"ctVal": "1", "lotSz": "1", "minSz": "1"}

    def get_balance_usdt(self):
        return 500.0

    def set_leverage(self, inst_id, leverage, td_mode):
        self.leverage_calls.append((inst_id, leverage, td_mode))
        return {"code": "0"}

    def place_order(self, **params):
        self.orders.append(params)
        return {"code": "0", "data": [{"ordId": "okx-order-1"}]}

    def place_algo_order(self, **params):
        self.algos.append(params)
        return {"code": "0", "data": [{"algoId": "okx-algo-1"}]}


def test_okx_executor_keeps_fee_buffer_for_full_margin_live_orders():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "false")
        db.set_setting("position_pct", "1")
        db.set_setting("default_leverage", "5")
        message_id = db.insert_message("x", "chartprime", "ChartPrime", "signal")
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="BTC",
            side="long",
            entry_low=100,
            entry_high=100,
            entry_note="limit order",
            stop_loss=90,
            take_profit=120,
        )
        client = FakeOkxClient()

        out = OkxExecutor(db, RiskEngine(db), client).open_with_sl_tp(result, message_id, 1)

        assert out["ok"] is True
        assert out["plan"]["margin_usdt"] == 500 * OKX_MARGIN_BUFFER_PCT
        assert out["plan"]["notional_usdt"] == 500 * OKX_MARGIN_BUFFER_PCT * 5
        assert out["plan"]["sz"] == "24"
        assert client.leverage_calls == [("BTC-USDT-SWAP", 5, "cross")]
        assert client.orders[0]["sz"] == "24"
        assert client.orders[0]["px"] == "100.0"
        assert client.algos[0]["ordType"] == "oco"
        assert client.algos[0]["tpTriggerPx"] == "120.0"
        assert client.algos[0]["slTriggerPx"] == "90.0"
        assert client.algos[0]["sz"] == "24"
        order = db.list_orders()[0]
        assert order["status"] == "live"
        assert order["okx_ord_id"] == "okx-order-1"


def test_okx_executor_market_note_uses_market_order_and_mark_for_sizing():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "false")
        db.set_setting("position_pct", "1")
        db.set_setting("default_leverage", "5")
        message_id = db.insert_message("dot", "988830102957736027", "ChartPrime", "signal")
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="DOT",
            side="long",
            entry_low=0.995,
            entry_high=1.013,
            entry_note="market",
            stop_loss=0.984,
            take_profit=1.016,
        )
        client = FakeOkxClient()
        client.get_ticker = lambda inst_id: 1.01

        out = OkxExecutor(db, RiskEngine(db), client).open_with_sl_tp(result, message_id, 1)

        assert out["ok"] is True
        assert out["plan"]["px"] == 1.01
        assert client.orders[0]["ordType"] == "market"
        assert "px" not in client.orders[0]
        order = db.list_orders()[0]
        assert order["ord_type"] == "market"


def test_okx_executor_uses_market_when_live_price_is_inside_entry_range_without_limit_text():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "false")
        db.set_setting("position_pct", "1")
        db.set_setting("default_leverage", "5")
        message_id = db.insert_message(
            "dot-range",
            "988830102957736027",
            "ChartPrime",
            "DOT @Crypto Signal LONG: 5-10x,ENTRY: 0.995-1.013,EXIT: 1.016,SL: 0.984",
        )
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="DOT",
            side="long",
            entry_low=0.995,
            entry_high=1.013,
            stop_loss=0.984,
            take_profit=1.016,
        )
        client = FakeOkxClient()
        client.get_ticker = lambda inst_id: 1.01

        out = OkxExecutor(db, RiskEngine(db), client).open_with_sl_tp(result, message_id, 1)

        assert out["ok"] is True
        assert out["plan"]["ord_type"] == "market"
        assert out["plan"]["px"] == 1.01
        assert client.orders[0]["ordType"] == "market"
        assert "px" not in client.orders[0]
        assert db.list_orders()[0]["ord_type"] == "market"


def test_okx_executor_keeps_explicit_limit_even_when_price_is_inside_range():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "false")
        db.set_setting("position_pct", "1")
        db.set_setting("default_leverage", "5")
        message_id = db.insert_message(
            "dot-limit",
            "988830102957736027",
            "ChartPrime",
            "DOT limit long: 0.995-1.013 SL: 0.984 TP: 1.016",
        )
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="DOT",
            side="long",
            entry_low=0.995,
            entry_high=1.013,
            stop_loss=0.984,
            take_profit=1.016,
        )
        client = FakeOkxClient()
        client.get_ticker = lambda inst_id: 1.01

        out = OkxExecutor(db, RiskEngine(db), client).open_with_sl_tp(result, message_id, 1)

        assert out["ok"] is True
        assert out["plan"]["ord_type"] == "limit"
        assert out["plan"]["px"] == (0.995 + 1.013) / 2
        assert client.orders[0]["ordType"] == "limit"
        assert client.orders[0]["px"] == "1.004"
        assert db.list_orders()[0]["ord_type"] == "limit"


def test_okx_executor_can_use_higher_exchange_leverage_with_lower_notional_sizing():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "false")
        db.set_setting("position_pct", "1")
        db.set_setting("max_leverage", "20")
        db.set_setting("channel_leverage_map", '{"chartprime": 20}')
        db.set_setting(OKX_CHANNEL_NOTIONAL_LEVERAGE_MAP_KEY, '{"chartprime": 5}')
        message_id = db.insert_message("x", "chartprime", "ChartPrime", "signal")
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="LIT",
            side="long",
            entry_low=1.6775,
            entry_high=1.6775,
            stop_loss=1.64,
            take_profit=1.702,
        )
        client = FakeOkxClient()
        client.get_balance_usdt = lambda: 400.0
        client.get_ticker = lambda inst_id: 1.6775

        out = OkxExecutor(db, RiskEngine(db), client).open_with_sl_tp(result, message_id, 1)

        assert out["ok"] is True
        assert out["plan"]["leverage"] == 20
        assert out["plan"]["notional_leverage"] == 5
        assert out["plan"]["notional_usdt"] == 400 * OKX_MARGIN_BUFFER_PCT * 5
        assert out["plan"]["margin_usdt"] == 400 * OKX_MARGIN_BUFFER_PCT * 5 / 20
        assert client.leverage_calls == [("LIT-USDT-SWAP", 20, "cross")]


def test_okx_close_position_rounds_partial_size_to_lot(monkeypatch):
    client = OkxClient.__new__(OkxClient)
    seen = {}
    monkeypatch.setattr(
        client,
        "get_positions",
        lambda: [{"instId": "LIT-USDT-SWAP", "pos": "965"}],
    )
    monkeypatch.setattr(
        client,
        "get_instrument",
        lambda inst_id: {"lotSz": "1", "minSz": "1"},
    )
    monkeypatch.setattr(
        client,
        "place_order",
        lambda **params: seen.update(params) or {"code": "0", "data": [{"ordId": "close-1"}]},
    )

    out = client.close_position("LIT-USDT-SWAP", 100 / 6)

    assert out["ok"] is True
    assert out["closed_size"] == 160
    assert seen["sz"] == "160"
    assert seen["reduceOnly"] == "true"
