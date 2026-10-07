import asyncio
import tempfile
from pathlib import Path

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.exchange import (
    create_exchange_client,
    exchange_configured,
    exchange_label,
    selected_exchange,
    selected_leverage,
    set_channel_exchange_map,
    set_channel_leverage_map,
)
from yoyo.copier.router.action_router import ActionRouter
from yoyo.copier.risk.engine import RiskEngine
from yoyo.copier.store.sqlite import Database

OPEN = """飞扬合约策略
具体产品：BTC
进行方向：做多
进场点位：78000-78300
止损点位：76000
止盈点位：80000"""


def test_selected_exchange_can_route_by_channel():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        set_channel_exchange_map(
            db,
            {
                "988830102957736027": "okx",
                "1226095564073205780": "gate",
                "1356581750914027590": "binance",
                "1131521990814089276": "gate_mia",
            },
        )

        assert selected_exchange(db, "988830102957736027") == "okx"
        assert selected_exchange(db, "1226095564073205780") == "gate"
        assert selected_exchange(db, "1356581750914027590") == "binance"
        assert selected_exchange(db, "1131521990814089276") == "gate_mia"
        assert selected_exchange(db, "unknown") == "okx"


def test_gate_alias_uses_dedicated_credentials(monkeypatch):
    monkeypatch.setenv("GATE_MIA_API_KEY", "mia-key")
    monkeypatch.setenv("GATE_MIA_SECRET_KEY", "mia-secret")
    monkeypatch.setenv("GATE_MIA_SETTLE", "usdt")
    monkeypatch.setenv("GATE_MIA_TESTNET", "false")

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        set_channel_exchange_map(db, {"1131521990814089276": "gate_mia"})

        client = create_exchange_client(db, channel_id="1131521990814089276")

        assert exchange_configured(db, "1131521990814089276") is True
        assert exchange_label(db, "1131521990814089276") == "Gate Arthur"
        assert client.api_key == "mia-key"
        assert client.secret_key == "mia-secret"


def test_router_passes_message_channel_to_exchange_factory(monkeypatch):
    seen = []

    class FakeExecutor:
        def open_with_sl_tp(self, result, message_id, signal_id):
            return {"ok": True, "dry_run": True, "plan": {"exchange": "gate"}}

    def fake_create_executor(db, risk, channel_id=None, exchange=None):
        seen.append(channel_id)
        return FakeExecutor()

    monkeypatch.setattr("yoyo.copier.router.action_router.create_exchange_executor", fake_create_executor)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "true")
        set_channel_exchange_map(db, {"1226095564073205780": "gate"})
        router = ActionRouter(db)
        msg_id = db.insert_message("m1", "1226095564073205780", "kol", OPEN)

        result = asyncio.run(router.process_message(msg_id, OPEN, "kol"))

        assert result.intent == "open"
        assert seen == ["1226095564073205780"]
        assert db.get_message(msg_id)["status"] == "executed"


def test_channel_leverage_can_differ_by_blogger():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        set_channel_leverage_map(
            db,
            {
                "988830102957736027": 5,
                "1226095564073205780": 3,
            },
        )

        assert selected_leverage(db, "988830102957736027") == 5
        assert selected_leverage(db, "1226095564073205780") == 3


def test_risk_open_position_limit_is_channel_scoped():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("max_open_positions", "1")
        old_message = db.insert_message("old", "1226095564073205780", "Woods", OPEN)
        db.insert_order(
            message_id=old_message,
            signal_id=None,
            inst_id="ETH-USDT-SWAP",
            side="buy",
            ord_type="limit",
            status="live",
        )
        new_message = db.insert_message("new", "1487843144950349824", "比特币米娅", OPEN)

        decision = RiskEngine(db).check(
            result=IntentResult(
                should_act=True,
                intent="open",
                confidence=0.95,
                symbol="BTC",
                side="long",
                entry_low=78000,
                entry_high=78300,
                stop_loss=76000,
            ),
            message_id=new_message,
        )

        assert decision.allowed is True


def test_router_overrides_stale_local_position_limit_with_exchange_snapshot(monkeypatch):
    sent = []

    async def fake_notify(*args, **kwargs):
        sent.append(kwargs.get("title") or (args[1] if len(args) > 1 else ""))
        return {"ok": True}

    class FakeAi:
        async def analyze(self, *args, **kwargs):
            return IntentResult(
                should_act=True,
                intent="open",
                confidence=0.95,
                symbol="BTC",
                side="long",
                entry_low=78000,
                entry_high=78300,
                stop_loss=76000,
            )

    class EmptyExchangeClient:
        def get_positions(self):
            return []

        def get_open_orders(self):
            return []

    class FakeExecutor:
        def open_with_sl_tp(self, result, message_id, signal_id):
            return {"ok": True, "dry_run": True}

    monkeypatch.setattr("yoyo.copier.router.action_router.exchange_configured", lambda *args, **kwargs: True)
    monkeypatch.setattr("yoyo.copier.router.action_router.create_exchange_client", lambda *args, **kwargs: EmptyExchangeClient())
    monkeypatch.setattr("yoyo.copier.router.action_router.create_exchange_executor", lambda *args, **kwargs: FakeExecutor())
    monkeypatch.setattr("yoyo.copier.router.action_router.notify_trade_pipeline", fake_notify)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("max_open_positions", "1")
        set_channel_exchange_map(db, {"1226095564073205780": "gate"})
        old_message = db.insert_message("old", "1226095564073205780", "Woods", OPEN)
        db.insert_order(
            message_id=old_message,
            signal_id=None,
            inst_id="ETH-USDT-SWAP",
            side="buy",
            ord_type="limit",
            status="live",
        )
        new_message = db.insert_message("new", "1226095564073205780", "Woods", OPEN)
        router = ActionRouter(db)
        router.ai = FakeAi()

        asyncio.run(router.process_message(new_message, OPEN, "Woods"))

        assert db.get_message(new_message)["status"] == "executed"
        assert db.list_orders(limit=10)[-1]["status"] == "stale"
        assert sent[-1] == "模拟下单已记录"
