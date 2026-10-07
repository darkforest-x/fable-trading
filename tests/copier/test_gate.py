import json
import tempfile
from pathlib import Path

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.gate.client import gate_contract, inst_id_from_gate
from yoyo.copier.gate.executor import GATE_CHANNEL_EXCHANGE_LEVERAGE_MAP_KEY, GateExecutor
from yoyo.copier.risk.engine import RiskEngine
from yoyo.copier.store.sqlite import Database


class FakeGateClient:
    settle = "usdt"

    def __init__(self):
        self.orders = []
        self.price_orders = []
        self.leverage_calls = []

    def get_instrument(self, inst_id):
        return {
            "contract": gate_contract(inst_id),
            "quanto_multiplier": "0.0001",
            "order_size_min": 1,
            "order_size_max": 1000000,
            "order_price_round": "0.1",
            "leverage_max": "125",
        }

    def get_ticker(self, inst_id):
        return 100000.0

    def get_balance_usdt(self):
        return 500.0

    def get_balance_summary(self):
        return {"total_usdt": 500.0, "available_usdt": 500.0, "unrealized_pnl": 0.0}

    def set_leverage(self, inst_id, lever, mgn_mode):
        self.leverage_calls.append((inst_id, lever, mgn_mode))
        return {"leverage": "0"}

    def place_order(self, params):
        self.orders.append(params)
        return {"id": "gate-order-1"}

    def place_price_order(self, params):
        self.price_orders.append(params)
        return {"id": f"trigger-{len(self.price_orders) + 1}"}

    def get_positions(self):
        return []


def open_result(side="long"):
    return IntentResult(
        should_act=True,
        intent="open",
        confidence=0.95,
        symbol="BTC",
        side=side,
        entry_low=100000,
        entry_high=100000,
        entry_note="limit order",
        stop_loss=99000,
        take_profit=101000,
        leverage_hint=5,
    )


def test_gate_contract_mapping():
    assert gate_contract("BTC-USDT-SWAP") == "BTC_USDT"
    assert gate_contract("ethusdt") == "ETH_USDT"
    assert inst_id_from_gate("SOL_USDT") == "SOL-USDT-SWAP"


def test_gate_executor_dry_run_uses_full_margin_times_leverage():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "true")
        db.set_setting("exchange", "gate")
        db.set_setting("position_pct", "1.0")
        executor = GateExecutor(db, RiskEngine(db), FakeGateClient())

        out = executor.open_with_sl_tp(open_result("long"), message_id=1, signal_id=1)

        assert out["ok"] is True
        assert out["dry_run"] is True
        assert out["plan"]["exchange"] == "gate"
        assert out["plan"]["notional_leverage"] == 5
        assert out["plan"]["leverage"] == 125
        assert out["plan"]["margin_usdt"] == 20
        assert out["plan"]["notional_usdt"] == 2500
        assert out["plan"]["sz"] == "250"
        order = db.list_orders()[0]
        assert order["status"] == "dry_run"
        saved_plan = json.loads(order["response_json"])
        assert saved_plan["contract"] == "BTC_USDT"


def test_gate_executor_live_short_places_close_triggers():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "false")
        db.set_setting("exchange", "gate")
        db.set_setting("position_pct", "1.0")
        client = FakeGateClient()
        executor = GateExecutor(db, RiskEngine(db), client)

        out = executor.open_with_sl_tp(open_result("short"), message_id=99, signal_id=1)

        assert out["ok"] is True
        assert client.leverage_calls == [("BTC-USDT-SWAP", 125, "cross")]
        assert client.orders[0]["contract"] == "BTC_USDT"
        assert client.orders[0]["size"] == -250
        assert client.orders[0]["price"] == "100000"
        assert len(client.price_orders) == 2
        tp, sl = client.price_orders
        assert tp["initial"]["size"] == 250
        assert tp["trigger"]["rule"] == 2
        assert sl["initial"]["size"] == 250
        assert sl["trigger"]["rule"] == 1
        order = db.list_orders()[0]
        assert order["status"] == "live"
        assert order["okx_ord_id"] == "gate-order-1"


def test_gate_executor_splits_notional_and_exchange_leverage():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "false")
        db.set_setting("exchange", "gate")
        db.set_setting("position_pct", "1.0")
        db.set_setting("max_leverage", "20")
        db.set_setting("channel_leverage_map", '{"woods": 12}')
        db.set_setting(GATE_CHANNEL_EXCHANGE_LEVERAGE_MAP_KEY, '{"woods": "max"}')
        message_id = db.insert_message("x", "woods", "Woods", "signal")
        client = FakeGateClient()
        client.get_balance_usdt = lambda: 100.0
        client.get_balance_summary = lambda: {
            "total_usdt": 500.0,
            "available_usdt": 100.0,
            "unrealized_pnl": 0.0,
        }
        executor = GateExecutor(db, RiskEngine(db), client)

        out = executor.open_with_sl_tp(open_result("long"), message_id=message_id, signal_id=1)

        assert out["ok"] is True
        assert out["plan"]["notional_leverage"] == 12
        assert out["plan"]["leverage"] == 125
        assert out["plan"]["notional_usdt"] == 6000
        assert out["plan"]["margin_usdt"] == 48
        assert out["plan"]["available_balance"] == 100
        assert out["plan"]["sz"] == "600"
        assert client.leverage_calls == [("BTC-USDT-SWAP", 125, "cross")]
        assert client.orders[0]["size"] == 600


def test_gate_client_sets_cross_leverage_limit(monkeypatch):
    from yoyo.copier.gate.client import GateClient

    client = GateClient(api_key="key", secret_key="secret")
    seen = {}

    def fake_request(method, path, *, params=None, private=False, body=None):
        seen.update({"method": method, "path": path, "params": params, "private": private})
        return {"leverage": "0", "cross_leverage_limit": "75"}

    monkeypatch.setattr(client, "_request", fake_request)

    out = client.set_leverage("BTC-USDT-SWAP", 75, "cross")

    assert out["cross_leverage_limit"] == "75"
    assert seen["params"] == {"leverage": "0", "cross_leverage_limit": "75"}


def test_gate_executor_skips_entry_far_from_market():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        client = FakeGateClient()
        result = open_result("long")
        result.entry_low = result.entry_high = 130000
        executor = GateExecutor(db, RiskEngine(db), client)

        out = executor.open_with_sl_tp(result, message_id=1, signal_id=1)

        assert out["ok"] is False
        assert out["skipped"] is True
        assert "偏离" in out["error"]
        assert client.orders == []


def test_gate_executor_repairs_decimal_shorthand_against_market():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "true")
        db.set_setting("position_pct", "1.0")
        client = FakeGateClient()
        client.get_ticker = lambda inst_id: 0.0879
        client.get_instrument = lambda inst_id: {
            "contract": gate_contract(inst_id),
            "quanto_multiplier": "1",
            "order_size_min": 1,
            "order_size_max": 1000000,
            "order_price_round": "0.00001",
            "leverage_max": "100",
        }
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="DOGE",
            side="long",
            entry_low=0.8775,
            entry_high=0.8775,
            stop_loss=0.856,
        )
        executor = GateExecutor(db, RiskEngine(db), client)

        out = executor.open_with_sl_tp(result, message_id=1, signal_id=1)

        assert out["ok"] is True
        assert out["plan"]["px"] == 0.08775
        assert out["plan"]["sl"] == 0.0856
        assert out["plan"]["price_scale_adjusted"] == 0.1
        assert result.entry_low == 0.08775
        assert result.stop_loss == 0.0856


def test_gate_executor_repairs_btc_k_shorthand_against_market():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "true")
        db.set_setting("position_pct", "1.0")
        client = FakeGateClient()
        client.get_ticker = lambda inst_id: 65420.0
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="BTC",
            side="long",
            entry_low=65.3,
            entry_high=65.4,
            stop_loss=63.5,
        )
        executor = GateExecutor(db, RiskEngine(db), client)

        out = executor.open_with_sl_tp(result, message_id=1, signal_id=1)

        assert out["ok"] is True
        assert out["plan"]["px"] == 65350
        assert out["plan"]["sl"] == 63500
        assert out["plan"]["price_scale_adjusted"] == 1000
        assert result.entry_low == 65300
        assert result.stop_loss == 63500


def test_gate_client_close_position_submits_reduce_only_market_order(monkeypatch):
    from yoyo.copier.gate.client import GateClient

    client = GateClient(api_key="key", secret_key="secret")
    monkeypatch.setattr(
        client,
        "get_positions",
        lambda: [{"instId": "VELVET-USDT-SWAP", "size": 17, "posSide": "long"}],
    )
    seen = []
    monkeypatch.setattr(
        client,
        "place_order",
        lambda params: seen.append(params) or {"id": "close-1"},
    )

    out = client.close_position("VELVET-USDT-SWAP")

    assert out["ok"] is True
    assert seen[0]["size"] == -17
    assert seen[0]["price"] == "0"
    assert seen[0]["reduce_only"] is True
