import tempfile
from pathlib import Path

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.binance.client import (
    binance_position_side,
    binance_symbol,
    inst_id_from_binance,
    is_ip_restricted_error,
    request_ip_from_error,
)
from yoyo.copier.binance.executor import BinanceExecutor
from yoyo.copier.binance.executor import BINANCE_CHANNEL_EXCHANGE_LEVERAGE_MAP_KEY
from yoyo.copier.risk.engine import RiskEngine
from yoyo.copier.store.sqlite import Database


class FakeBinanceClient:
    def __init__(self):
        self.orders = []
        self.leverage = []
        self.health = {"ok": True, "balance": {"total_usdt": 500, "available_usdt": 500, "unrealized_pnl": 0}}

    def get_ticker(self, inst_id):
        return 100.0

    def get_instrument(self, inst_id):
        return {
            "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"}
            ]
        }

    def get_balance_usdt(self):
        return 500.0

    def get_balance_summary(self):
        return {"total_usdt": 500.0, "available_usdt": 500.0, "unrealized_pnl": 0.0}

    def get_max_leverage(self, inst_id, fallback=125):
        return 125

    def set_leverage(self, inst_id, leverage, td_mode):
        self.leverage.append((inst_id, leverage, td_mode))
        return {"leverage": leverage}

    def place_order(self, params):
        self.orders.append(params)
        return {"orderId": len(self.orders)}

    def place_algo_order(self, params):
        self.orders.append(params)
        return {"algoId": len(self.orders)}

    def health_check(self):
        return self.health


def test_binance_symbol_mapping():
    assert binance_symbol("BTC-USDT-SWAP") == "BTCUSDT"
    assert inst_id_from_binance("ETHUSDT") == "ETH-USDT-SWAP"
    assert binance_position_side("long") == "LONG"
    assert binance_position_side("short") == "SHORT"


def test_binance_ip_restriction_error_is_detected():
    error = "Invalid API-key, IP, or permissions for action, request ip: 104.193.10.168"

    assert is_ip_restricted_error(error) is True
    assert request_ip_from_error(error) == "104.193.10.168"


def test_binance_executor_uses_full_margin_times_five():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("position_pct", "1")
        db.set_setting("dry_run", "false")
        db.set_setting("channel_leverage_map", '{"fly": 5}')
        message_id = db.insert_message("x", "fly", "fly", "signal")
        client = FakeBinanceClient()
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.99,
            symbol="BTC",
            side="long",
            entry_low=100,
            entry_high=100,
            stop_loss=90,
            take_profit=120,
        )

        out = BinanceExecutor(db, RiskEngine(db), client).open_with_sl_tp(result, message_id, 1)

        assert out["ok"] is True
        assert out["plan"]["notional_leverage"] == 5
        assert out["plan"]["leverage"] == 125
        assert out["plan"]["margin_usdt"] == 20
        assert out["plan"]["notional_usdt"] == 2500
        assert out["plan"]["sz"] == "25"
        assert client.leverage == [("BTC-USDT-SWAP", 125, "cross")]
        assert client.orders[0]["type"] == "LIMIT"
        assert client.orders[0]["positionSide"] == "LONG"
        assert client.orders[1]["type"] == "STOP_MARKET"
        assert client.orders[1]["positionSide"] == "LONG"
        assert client.orders[2]["type"] == "TAKE_PROFIT_MARKET"
        assert client.orders[2]["positionSide"] == "LONG"


def test_binance_executor_short_uses_short_position_side():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("position_pct", "1")
        db.set_setting("dry_run", "false")
        db.set_setting("max_leverage", "12")
        db.set_setting("channel_leverage_map", '{"fly": 12}')
        message_id = db.insert_message("x", "fly", "fly", "signal")
        client = FakeBinanceClient()
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.99,
            symbol="ETH",
            side="short",
            entry_low=100,
            entry_high=100,
            stop_loss=110,
            take_profit=80,
        )

        out = BinanceExecutor(db, RiskEngine(db), client).open_with_sl_tp(result, message_id, 1)

        assert out["ok"] is True
        assert out["plan"]["notional_leverage"] == 12
        assert out["plan"]["leverage"] == 125
        assert out["plan"]["position_side"] == "SHORT"
        assert client.orders[0]["side"] == "SELL"
        assert client.orders[0]["positionSide"] == "SHORT"
        assert client.orders[1]["side"] == "BUY"
        assert client.orders[1]["positionSide"] == "SHORT"
        assert client.orders[2]["side"] == "BUY"
        assert client.orders[2]["positionSide"] == "SHORT"


def test_binance_executor_splits_notional_and_exchange_leverage():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("position_pct", "1")
        db.set_setting("dry_run", "false")
        db.set_setting("max_leverage", "20")
        db.set_setting("channel_leverage_map", '{"fly": 12}')
        db.set_setting(BINANCE_CHANNEL_EXCHANGE_LEVERAGE_MAP_KEY, '{"fly": 50}')
        message_id = db.insert_message("x", "fly", "fly", "signal")
        client = FakeBinanceClient()
        client.get_balance_usdt = lambda: 0.2
        client.get_balance_summary = lambda: {
            "total_usdt": 500.0,
            "available_usdt": 0.2,
            "unrealized_pnl": 0.0,
        }
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.99,
            symbol="BTC",
            side="short",
            entry_low=100,
            entry_high=100,
            stop_loss=110,
            take_profit=80,
        )

        out = BinanceExecutor(db, RiskEngine(db), client).open_with_sl_tp(result, message_id, 1)

        assert out["ok"] is True
        assert out["plan"]["notional_leverage"] == 12
        assert out["plan"]["leverage"] == 50
        assert out["plan"]["notional_usdt"] == 6000
        assert out["plan"]["margin_usdt"] == 120
        assert out["plan"]["available_balance"] == 0.2
        assert client.leverage == [("BTC-USDT-SWAP", 50, "cross")]
        assert client.orders[0]["quantity"] == "60"


def test_binance_executor_skips_when_ip_whitelist_rejects():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("position_pct", "1")
        db.set_setting("dry_run", "false")
        message_id = db.insert_message("x", "fly", "fly", "signal")
        client = FakeBinanceClient()
        client.health = {
            "ok": False,
            "ip_restricted": True,
            "request_ip": "104.193.10.168",
            "error": "Binance IP 白名单拒绝。当前出口 IP: 104.193.10.168",
        }
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.99,
            symbol="BTC",
            side="long",
            entry_low=100,
            entry_high=100,
            stop_loss=90,
        )

        out = BinanceExecutor(db, RiskEngine(db), client).open_with_sl_tp(result, message_id, 1)

        assert out["ok"] is False
        assert out["skipped"] is True
        assert "104.193.10.168" in out["error"]
        assert client.orders == []
