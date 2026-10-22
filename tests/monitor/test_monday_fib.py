"""Weekly level/notification lifecycle tests; no live Telegram requests."""
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from yoyo.monitor.monday_fib import FibBook, baseline, deliver_once, week_window, HOUR, MINUTE
from yoyo.monitor.monday_fib_service import MondayFibService, install


def ms(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)


NOW = ms("2026-10-21T12:30:00")
SYMBOL = "BTC-USDT-SWAP"


def candle(t, high=110, low=100, close=105, confirmed="1"):
    return [str(t), "105", str(high), str(low), str(close), "1", "1", "105", confirmed]


def history(now=NOW):
    start = week_window(now)["start"]
    return [candle(t, confirmed="0" if t == now // HOUR * HOUR else "1")
            for t in range(start, now // HOUR * HOUR + 1, HOUR)]


def ready_service(tmp_path):
    service = MondayFibService(tmp_path)
    service.symbols = (SYMBOL,)
    service.connected = True
    levels = baseline(history(), NOW)
    service.levels[SYMBOL] = {**levels, "epoch": 0}
    service.client.clock = lambda: NOW
    return service, levels


def test_ny_week_and_dst_do_not_reuse_previous_monday():
    winter = week_window(ms("2026-11-04T12:00:00"))
    summer = week_window(NOW)
    assert (winter["start"] // HOUR) % 24 == 5
    assert (summer["start"] // HOUR) % 24 == 4
    before_fall = week_window(ms("2026-10-30T12:00:00"))
    assert before_fall["expires"] - before_fall["start"] == 169 * HOUR
    monday = ms("2026-10-26T12:00:00")
    assert baseline(history(), monday)["state"] == "developing"


def test_levels_only_use_closed_monday_and_require_complete_history():
    rows = history()
    levels = baseline(rows, NOW)
    assert levels["state"] == "ready"
    assert levels["upper"] == pytest.approx(116.18)
    assert levels["lower"] == pytest.approx(93.82)
    rows[-1] = candle(int(rows[-1][0]), high=125, low=90, confirmed="0")
    changed = baseline(rows, NOW)
    assert changed["high"] == 110 and changed["low"] == 100
    assert set(changed["historical_touches"]) == {"upper", "lower"}
    assert baseline(rows[1:], NOW)["state"] == "missing_monday"
    assert baseline(rows[:-1], NOW)["state"] == "missing_history"
    rows[0][-1] = "0"
    assert baseline(rows, NOW)["state"] == "missing_monday"


def test_intraminute_wick_consumes_once_and_restart_preserves_dedup(tmp_path):
    service, levels = ready_service(tmp_path)
    service.book.set_enabled(True, NOW - MINUTE)
    service.on_candle(SYMBOL, candle(NOW, high=120, close=105, confirmed="0"), NOW + 1)
    service.on_candle(SYMBOL, candle(NOW, high=121, close=105, confirmed="0"), NOW + 2)
    assert len(service.book.records()) == 1
    assert service.book.records()[0]["status"] == "pending"
    assert service.book.records()[0]["price"] == 105  # close is inside, wick still touches
    assert not FibBook(tmp_path).touch(levels, SYMBOL, "upper", 119, NOW + 3, NOW, "live")
    service.on_candle(SYMBOL, candle(NOW, high=121, low=90, confirmed="0"), NOW + 3)
    assert len(service.book.records()) == 2


def test_off_default_and_enable_never_replays_history(tmp_path):
    book = FibBook(tmp_path)
    levels = baseline(history(), NOW)
    assert book.subscription()["enabled"] is False
    book.touch(levels, SYMBOL, "upper", 120, NOW, NOW, "history")
    book.set_enabled(True, NOW + 1)
    assert book.claim(NOW + 2) is None
    assert not book.touch(levels, SYMBOL, "upper", 120, NOW + MINUTE, NOW + MINUTE, "live")
    # A first snapshot of the already-open activation candle is ambiguous.
    book.touch(levels, SYMBOL, "lower", 90, NOW + 10, NOW, "live")
    assert book.claim(NOW + 11) is None


def test_continuous_observation_can_alert_within_activation_minute(tmp_path):
    service, _ = ready_service(tmp_path)
    service.book.set_enabled(True, NOW + 5)
    service.on_candle(SYMBOL, candle(NOW, confirmed="0"), NOW + 10)
    service.on_candle(SYMBOL, candle(NOW, high=120, confirmed="0"), NOW + 20)
    assert service.book.records()[0]["status"] == "pending"


def test_reenable_disables_old_pending_and_unknown_not_retried(tmp_path):
    book = FibBook(tmp_path)
    levels = baseline(history(), NOW)
    book.set_enabled(True, NOW - MINUTE)
    book.touch(levels, SYMBOL, "upper", 120, NOW, NOW, "live")
    book.set_enabled(False, NOW + 1)
    book.set_enabled(True, NOW + 2)
    assert book.claim(NOW + 3) is None
    book.touch(levels, SYMBOL, "lower", 90, NOW + MINUTE, NOW + MINUTE, "live")
    calls = []
    def timeout(*args, **kwargs):
        calls.append(1)
        raise TimeoutError()
    assert deliver_once(book, ("fake", "fake"), timeout, NOW + MINUTE + 1)
    assert not deliver_once(book, ("fake", "fake"), timeout, NOW + MINUTE + 2)
    assert calls == [1]
    assert book.records()[0]["status"] == "unknown"


@pytest.mark.parametrize("code,payload,expected", [
    (200, {"ok": True, "result": {"message_id": 123}}, "sent"),
    (200, {"ok": True, "result": {}}, "unknown"),
    (429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 5}}, "pending"),
    (403, {"ok": False, "error_code": 403}, "failed"),
    (500, {"ok": False}, "unknown"),
])
def test_sender_receipts(tmp_path, code, payload, expected):
    book = FibBook(tmp_path)
    book.set_enabled(True, NOW - MINUTE)
    book.touch(baseline(history(), NOW), SYMBOL, "upper", 120, NOW, NOW, "live")
    class Response:
        status_code = code
        def json(self):
            return payload
    def sender(url, **kwargs):
        assert kwargs["allow_redirects"] is False
        assert "位置提醒" in kwargs["json"]["text"]
        return Response()
    deliver_once(book, ("fake", "fake"), sender, NOW + 1)
    assert book.records()[0]["status"] == expected


def test_expiry_restart_and_week_rollover(tmp_path):
    service, levels = ready_service(tmp_path)
    service.book.set_enabled(True, NOW - MINUTE)
    service.book.touch(levels, SYMBOL, "upper", 120, NOW, NOW, "live")
    assert service.book.claim(NOW + 91_000) is None
    service.book.touch(levels, SYMBOL, "lower", 90, NOW + MINUTE, NOW + MINUTE, "live")
    assert service.book.claim(NOW + MINUTE + 1)
    service.book.recover()
    assert service.book.records()[0]["status"] == "unknown"
    monday = ms("2026-10-26T12:00:00")
    service.on_candle(SYMBOL, candle(monday, high=120, confirmed="0"), monday + 1)
    assert len(service.book.records()) == 2


def test_stream_gap_requires_history_reconciliation_and_stale_visible(tmp_path):
    service, _ = ready_service(tmp_path)
    service.on_candle(SYMBOL, candle(NOW, confirmed="0"), NOW + 1)
    service.on_candle(SYMBOL, candle(NOW + 3 * MINUTE, high=120, confirmed="0"), NOW + 3 * MINUTE + 1)
    assert service.levels[SYMBOL]["state"] == "recovering"
    assert service.book.records() == []
    service.connected = False
    snapshot = service.snapshot()
    assert snapshot["items"][0]["stale"]
    assert snapshot["counts"]["ready"] == 0


def test_snapshot_keeps_monday_extrema_separate_from_live_candle(tmp_path):
    service, levels = ready_service(tmp_path)
    service.on_candle(SYMBOL, candle(NOW, high=125, low=90, confirmed="0"), NOW + 1)
    item = service.snapshot()["items"][0]
    assert item["high"] == levels["high"] == 110
    assert item["low"] == levels["low"] == 100


def test_weekly_all_market_status_is_not_capped_at_1000_sides(tmp_path):
    book = FibBook(tmp_path)
    levels = baseline(history(), NOW)
    with book.db() as db:
        db.executemany("INSERT INTO touches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (levels["week"], f"C{i}-USDT-SWAP", side, levels[side], 105, NOW, NOW,
             "history", 0, "history", NOW, None, None)
            for i in range(507) for side in ("upper", "lower")])
    assert len(book.records(levels["week"])) == 1014
    assert len(book.records(levels["week"], limit=40)) == 40


def test_api_mutation_requires_origin_explicit_boolean_and_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("yoyo.monitor.monday_fib_service.credentials", lambda: None)
    service, _ = ready_service(tmp_path)
    app = FastAPI()
    install(app, service)
    client = TestClient(app)
    assert client.get("/api/monday-fib").json()["telegram"]["enabled"] is False
    path = "/api/monday-fib/telegram"
    assert client.put(path, json={"enabled": True}).status_code == 403
    headers = {"Origin": "http://testserver", "X-Spike-Action": "update-monday-fib-telegram"}
    assert client.put(path, json={"enabled": "true"}, headers=headers).status_code == 422
    assert client.put(path, json={"enabled": True}, headers=headers).status_code == 409
    monkeypatch.setattr("yoyo.monitor.monday_fib_service.credentials", lambda: ("fake-secret", "fake-chat"))
    response = client.put(path, json={"enabled": True}, headers=headers)
    assert response.json()["telegram"]["enabled"] is True
    assert "fake-secret" not in response.text and "fake-chat" not in response.text
