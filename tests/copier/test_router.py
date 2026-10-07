import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.router.action_router import ActionRouter, _execution_error_detail, _open_success_title_detail
from yoyo.copier.store.sqlite import Database

OPEN = """飞扬合约策略
具体产品：BTC
进行方向：做空
进场点位：78000-78300
止损点位：80000
止盈点位：74631"""


def test_router_open_dry_run():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "true")
        db.set_setting("max_entry_deviation_pct", "0")
        router = ActionRouter(db)
        msg_id = db.insert_message("1", "ch", "kol", OPEN)
        result = asyncio.run(router.process_message(msg_id, OPEN, "kol"))
        assert result.intent == "open"
        msg = db.get_message(msg_id)
        assert msg["status"] == "executed"
        orders = db.list_orders()
        assert len(orders) == 1
        assert orders[0]["status"] == "dry_run"


def test_router_noise():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        router = ActionRouter(db)
        text = "今日没有入场不用急"
        msg_id = db.insert_message("2", "ch", "kol", text)
        result = asyncio.run(router.process_message(msg_id, text))
        assert result.intent == "noise"
        assert db.get_message(msg_id)["status"] == "skipped"


def test_router_open_triggers_notification(monkeypatch):
    sent = []

    async def fake_notify(*args, **kwargs):
        sent.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.router.action_router.notify_trade_pipeline", fake_notify)
    monkeypatch.setattr("yoyo.copier.router.action_router.notify_signal_detected", fake_notify)
    monkeypatch.setattr("yoyo.copier.router.action_router.notify_trade_update", fake_notify)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "true")
        db.set_setting("max_entry_deviation_pct", "0")
        router = ActionRouter(db)
        msg_id = db.insert_message("3", "ch", "kol", OPEN)
        result = asyncio.run(router.process_message(msg_id, OPEN, "kol"))

        assert result.intent == "open"
        assert sent
        assert sent[0].get("message_id") == msg_id
        assert sent[-1]["title"] == "模拟下单已记录"


def test_open_success_title_names_limit_and_market_orders():
    assert _open_success_title_detail("Gate", {"ok": True, "plan": {"px": 62.46}}) == (
        "Gate 限价单提交成功",
        "限价单与止盈止损已提交",
    )
    assert _open_success_title_detail("Binance", {"ok": True, "plan": {"entry_note": "market"}}) == (
        "Binance 市价单下单成功",
        "市价单与止盈止损已提交",
    )


def test_router_blocked_channel_never_executes_trade(monkeypatch):
    sent = []

    async def fake_notify(*args, **kwargs):
        sent.append(kwargs)
        return {"ok": True}

    def fail_executor(*args, **kwargs):
        raise AssertionError("blocked channel should not create exchange executor")

    monkeypatch.setattr("yoyo.copier.router.action_router.notify_trade_pipeline", fake_notify)
    monkeypatch.setattr("yoyo.copier.router.action_router.notify_signal_detected", fake_notify)
    monkeypatch.setattr("yoyo.copier.router.action_router.create_exchange_executor", fail_executor)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("blocked_trade_channels", json.dumps(["1226095564073205780"]))
        router = ActionRouter(db)

        async def fake_analyze(*args, **kwargs):
            return IntentResult(
                intent="open",
                should_act=True,
                confidence=0.98,
                symbol="AIO",
                side="long",
                entry_low=0.3,
                entry_high=0.31,
                stop_loss=0.28,
            )

        router.ai.analyze = fake_analyze
        text = "longed AIO at 0.3 sl: 0.28"
        msg_id = db.insert_message("blocked-open", "1226095564073205780", "Woods", text)
        result = asyncio.run(router.process_message(msg_id, text, "Woods"))

        assert result.intent == "open"
        assert db.get_message(msg_id)["status"] == "skipped"
        assert db.list_orders() == []
        assert sent[-1]["title"] == "博主已拉黑（未执行）"
        assert "交易黑名单" in sent[-1]["detail"]


def test_router_trade_update_triggers_notification(monkeypatch):
    sent = []

    async def fake_notify(*args, **kwargs):
        sent.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.router.action_router.notify_trade_pipeline", fake_notify)
    monkeypatch.setattr("yoyo.copier.router.action_router.notify_signal_detected", fake_notify)
    monkeypatch.setattr("yoyo.copier.router.action_router.notify_trade_update", fake_notify)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        router = ActionRouter(db)
        text = "#BTC 1st TARGET HIT at 89700 Duration: 20 mins"
        msg_id = db.insert_message("4", "ch", "kol", text)
        result = asyncio.run(router.process_message(msg_id, text, "kol"))

        assert result.intent in {"partial_close", "close"}
        assert db.get_message(msg_id)["status"] == "skipped"
        assert sent
        assert sent[-1]["message_id"] == msg_id


def test_router_recovery_open_signal_never_executes(monkeypatch):
    sent = []

    async def fake_notify(*args, **kwargs):
        sent.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.router.action_router.notify_trade_pipeline", fake_notify)
    monkeypatch.setattr("yoyo.copier.router.action_router.notify_signal_detected", fake_notify)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "false")
        router = ActionRouter(db)
        msg_id = db.insert_message("recovery-1", "ch", "kol", OPEN)
        result = asyncio.run(
            router.process_message(msg_id, OPEN, "kol", allow_execute=False)
        )

        assert result.intent == "open"
        assert db.get_message(msg_id)["status"] == "skipped"
        assert db.list_orders() == []
        assert sent[-1]["title"] == "离线补抓信号（未执行）"


def test_router_executes_explicit_full_close(monkeypatch):
    sent = []
    closed = []

    class FakeClient:
        def close_position(self, inst_id, close_pct=100):
            closed.append((inst_id, close_pct))
            return {"ok": True, "closed_size": 17}

    async def fake_notify(*args, **kwargs):
        sent.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.router.action_router.create_exchange_client", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr("yoyo.copier.router.action_router.notify_trade_pipeline", fake_notify)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        router = ActionRouter(db)
        async def fake_analyze(*args, **kwargs):
            return IntentResult(
                intent="close",
                should_act=False,
                confidence=0.95,
                symbol="VELVET",
                close_pct=100,
                summary="VELVET 已全平",
            )
        router.ai.analyze = fake_analyze
        text = "VELVET: Closed in small profit (100%)"
        msg_id = db.insert_message("close-1", "1226095564073205780", "Woods", text)
        result = asyncio.run(router.process_message(msg_id, text, "Woods"))

        assert result.intent == "close"
        assert closed == [("VELVET-USDT-SWAP", 100.0)]
        assert db.get_message(msg_id)["status"] == "executed"
        assert sent[-1]["title"] == "OKX 交易更新执行成功"


def test_router_executes_partial_close_and_moves_stop(monkeypatch):
    sent = []
    actions = []

    class FakeClient:
        def close_position(self, inst_id, close_pct=100):
            actions.append(("close", inst_id, close_pct))
            return {"ok": True, "closed_size": 5}

        def update_stop_loss(self, inst_id, trigger_px):
            actions.append(("sl", inst_id, trigger_px))
            return {"ok": True, "trigger_px": trigger_px}

    async def fake_notify(*args, **kwargs):
        sent.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.router.action_router.create_exchange_client", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr("yoyo.copier.router.action_router.notify_trade_pipeline", fake_notify)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        router = ActionRouter(db)

        async def fake_analyze(*args, **kwargs):
            return IntentResult(
                intent="partial_close",
                should_act=True,
                confidence=0.98,
                symbol="BTC",
                close_pct=50,
                stop_loss=62700,
            )

        router.ai.analyze = fake_analyze
        text = "目前盈利1000点，止盈50%，剩下的止损移动到成本价62700"
        msg_id = db.insert_message("partial-1", "1356581750914027590", "比特币飞扬", text)
        asyncio.run(router.process_message(msg_id, text, "比特币飞扬"))

        assert actions == [
            ("close", "BTC-USDT-SWAP", 50.0),
            ("sl", "BTC-USDT-SWAP", 62700),
        ]
        assert db.get_message(msg_id)["status"] == "executed"
        assert "平仓 50%" in sent[-1]["detail"]


def test_router_reports_partial_close_when_stop_update_fails(monkeypatch):
    sent = []

    class FakeClient:
        def close_position(self, inst_id, close_pct=100):
            return {"ok": True, "closed_size": 5}

        def update_stop_loss(self, inst_id, trigger_px):
            return {"ok": False, "response": {"code": -2021, "msg": "Order would immediately trigger"}}

    async def fake_notify(*args, **kwargs):
        sent.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.router.action_router.create_exchange_client", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr("yoyo.copier.router.action_router.notify_trade_pipeline", fake_notify)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        router = ActionRouter(db)

        async def fake_analyze(*args, **kwargs):
            return IntentResult(
                intent="partial_close",
                should_act=True,
                confidence=0.98,
                symbol="BTC",
                close_pct=50,
                stop_loss=62700,
            )

        router.ai.analyze = fake_analyze
        text = "BTC: close 50%, move stop to 62700"
        msg_id = db.insert_message("partial-sl-fail", "1356581750914027590", "比特币飞扬", text)
        asyncio.run(router.process_message(msg_id, text, "比特币飞扬"))

        assert db.get_message(msg_id)["status"] == "error"
        assert sent[-1]["title"].endswith("交易更新失败")
        assert "平仓已成功" in sent[-1]["detail"]
        assert "-2021: Order would immediately trigger" in sent[-1]["detail"]


def test_router_moves_stop_to_exchange_position_average(monkeypatch):
    sent = []
    actions = []

    class FakeClient:
        def get_positions(self):
            return [{"instId": "ETH-USDT-SWAP", "avgPx": "1634.65"}]

        def update_stop_loss(self, inst_id, trigger_px):
            actions.append((inst_id, trigger_px))
            return {"ok": True, "trigger_px": trigger_px}

    async def fake_notify(*args, **kwargs):
        sent.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.router.action_router.create_exchange_client", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr("yoyo.copier.router.action_router.notify_trade_pipeline", fake_notify)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        router = ActionRouter(db)

        async def fake_analyze(*args, **kwargs):
            return IntentResult(
                intent="update_sl",
                should_act=True,
                confidence=0.99,
                symbol="ETH",
                entry_note="break_even",
            )

        router.ai.analyze = fake_analyze
        text = "ETH: Stops moved to BE"
        msg_id = db.insert_message("be-1", "1226095564073205780", "Woods", text)
        asyncio.run(router.process_message(msg_id, text, "Woods"))

        assert actions == [("ETH-USDT-SWAP", 1634.65)]
        assert db.get_message(msg_id)["status"] == "executed"
        assert "1634.65" in sent[-1]["detail"]


def test_execution_error_detail_extracts_exchange_codes():
    assert _execution_error_detail({"response": {"code": -2015, "msg": "Invalid API-key"}}) == "-2015: Invalid API-key"
    assert _execution_error_detail(
        {"response": {"code": "1", "data": [{"sCode": "51008", "sMsg": "Insufficient margin"}]}}
    ) == "保证金不足：交易所可用 USDT 不足，或下单/止损单需要额外手续费与冻结余量；51008: Insufficient margin"
    assert _execution_error_detail({"response": {"label": "INVALID_PARAM", "message": "bad size"}}) == "INVALID_PARAM: bad size"
    assert _execution_error_detail(
        {"error": "request failed", "response": {"code": -2015, "msg": "Invalid API-key"}}
    ) == "request failed；-2015: Invalid API-key"
    assert "Hedge Mode" in _execution_error_detail(
        {"response": {"code": -4061, "msg": "Order's position side does not match user's setting."}}
    )


def test_router_chartprime_targets_close_only_new_equal_slices(monkeypatch):
    actions = []

    class FakeClient:
        def close_position(self, inst_id, close_pct=100):
            actions.append((inst_id, close_pct))
            return {"ok": True, "closed_size": 1}

    async def fake_notify(*args, **kwargs):
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.router.action_router.create_exchange_client", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr("yoyo.copier.router.action_router.notify_trade_pipeline", fake_notify)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        router = ActionRouter(db)
        for external_id, text in [
            ("tp1", "#LINK 1st TARGET HIT at 7.91"),
            ("tp2", "#LINK 2nd TARGET HIT at 7.95"),
            ("tp2-repeat", "#LINK 2nd TARGET HIT at 7.95"),
        ]:
            msg_id = db.insert_message(external_id, "988830102957736027", "ChartPrime", text)
            asyncio.run(router.process_message(msg_id, text, "ChartPrime"))

        assert actions[0][0] == "LINK-USDT-SWAP"
        assert actions[0][1] == pytest.approx(100 / 6)
        assert actions[1] == ("LINK-USDT-SWAP", 20.0)
        assert len(actions) == 2


def test_router_chartprime_target_hit_does_not_double_close_when_tp_protected(monkeypatch):
    actions = []
    sent = []

    class FakeClient:
        def get_open_triggers(self):
            return [
                {
                    "instId": "LIT-USDT-SWAP",
                    "ordType": "oco",
                    "state": "live",
                    "tpTriggerPx": "1.71",
                    "slTriggerPx": "1.64",
                }
            ]

        def close_position(self, inst_id, close_pct=100):
            actions.append((inst_id, close_pct))
            return {"ok": True, "closed_size": 1}

    async def fake_notify(*args, **kwargs):
        sent.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.router.action_router.create_exchange_client", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr("yoyo.copier.router.action_router.notify_trade_pipeline", fake_notify)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        router = ActionRouter(db)
        text = "#LIT 1st TARGET HIT at 1.7020 Duration: 50 mins"
        msg_id = db.insert_message("tp1", "988830102957736027", "ChartPrime", text)

        asyncio.run(router.process_message(msg_id, text, "ChartPrime"))

        assert actions == []
        assert db.get_message(msg_id)["status"] == "executed"
        assert db.get_setting("target_level:988830102957736027:LIT") == "1"
        assert "仅记录进度" in sent[-1]["detail"]
