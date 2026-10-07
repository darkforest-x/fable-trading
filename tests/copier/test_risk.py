import tempfile
from pathlib import Path

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.risk.engine import RiskEngine
from yoyo.copier.store.sqlite import Database


def test_risk_rejects_no_stop_loss():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("require_stop_loss", "true")
        engine = RiskEngine(db)
        r = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.9,
            symbol="BTC",
            side="short",
            stop_loss=None,
        )
        d = engine.check(r)
        assert not d.allowed
        assert "止损" in d.reason


def test_risk_allows_arthur_manual_fill_without_stop_marker():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        message_id = db.insert_message(
            "discord-1",
            "1131521990814089276",
            "Arthur",
            "Longed BTC 1%",
        )
        engine = RiskEngine(db)
        r = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="BTC",
            side="long",
            entry_note="market; allow_no_stop_loss",
            stop_loss=None,
        )

        d = engine.check(r, message_id)

        assert d.allowed


def test_risk_rejects_no_stop_marker_outside_arthur_channel():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        message_id = db.insert_message(
            "discord-2",
            "988830102957736027",
            "ChartPrime",
            "Longed BTC 1%",
        )
        engine = RiskEngine(db)
        r = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="BTC",
            side="long",
            entry_note="market; allow_no_stop_loss",
            stop_loss=None,
        )

        d = engine.check(r, message_id)

        assert not d.allowed
        assert "止损" in d.reason


def test_risk_kill_switch():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("kill_switch", "true")
        engine = RiskEngine(db)
        r = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="BTC",
            side="short",
            stop_loss=80000,
        )
        assert not engine.check(r).allowed


def test_risk_rejects_stop_loss_on_wrong_side():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("symbols_whitelist", "[]")
        engine = RiskEngine(db)
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="NEAR",
            side="long",
            entry_low=1.885,
            entry_high=1.926,
            stop_loss=2.0,
        )

        decision = engine.check(result)

        assert not decision.allowed
        assert "必须低于入场价" in decision.reason


def test_default_position_pct_uses_full_available_margin():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        assert RiskEngine(db).get_position_pct() == 1.0


def test_risk_rejects_spot_signal_for_swap_executor():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("symbols_whitelist", "[]")
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="NEAR",
            side="long",
            entry_low=1.8,
            entry_high=1.9,
            entry_note="spot limit",
            stop_loss=1.7,
        )

        decision = RiskEngine(db).check(result)

        assert not decision.allowed
        assert "现货信号" in decision.reason


def test_risk_rejects_candle_close_condition_not_supported():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("symbols_whitelist", "[]")
        result = IntentResult(
            should_act=True,
            intent="open",
            confidence=0.95,
            symbol="BTC",
            side="long",
            entry_low=100,
            entry_high=101,
            entry_note="2 H1 candle closures below 90",
            stop_loss=90,
        )

        decision = RiskEngine(db).check(result)

        assert not decision.allowed
        assert "K 线收盘确认" in decision.reason
