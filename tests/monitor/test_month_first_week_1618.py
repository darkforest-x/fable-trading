from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import threading
from unittest.mock import patch

from fastapi import FastAPI

from yoyo.monitor.month_first_week_1618 import (
    EVENT_TYPE, FRESH_MS, HOUR_MS, MINUTE_MS, MonthFirstWeekBook,
    closed_hour_touches, expected_hour_opens, make_anchor, month_window,
)
from yoyo.monitor.month_first_week_1618_service import MonthFirstWeek1618Service, install
from yoyo.discipline_app.notification_journal import valid_month_touch


UTC = timezone.utc


def ms(year, month, day, hour=0, minute=0, second=0):
    return int(datetime(year, month, day, hour, minute, second, tzinfo=UTC).timestamp() * 1000)


def source_rows(window, through_ms, *, high=100.0, low=90.0, post_high=98.0, post_low=92.0):
    rows = []
    current_hour = through_ms // HOUR_MS * HOUR_MS
    for stamp in range(window["start_ms"], current_hour, HOUR_MS):
        first_week = stamp < window["ready_ms"]
        h, l = (high, low) if first_week else (post_high, post_low)
        rows.append({"t": stamp, "o": (h + l) / 2, "h": h, "l": l, "c": (h + l) / 2, "v": 1.0})
    return rows


def raw_minute_rows(now_ms, *, high=98.0, low=92.0):
    hour_start = now_ms // HOUR_MS * HOUR_MS
    minute_open = now_ms // MINUTE_MS * MINUTE_MS
    rows = []
    for stamp in range(hour_start, minute_open + MINUTE_MS, MINUTE_MS):
        current = stamp == minute_open
        rows.append([str(stamp), "95", str(high), str(low), "95", "1", "1", "1", "0" if current else "1"])
    return rows


class FakeClient:
    include_open = True

    def __init__(self, now_ms, rows, minutes):
        self.now_ms = now_ms
        self.rows = rows
        self.minutes = minutes
        self.history_limits = []
        self.paths = []

    def clock(self):
        return self.now_ms

    def candles(self, symbol, timeframe, previous=None, limit=720):
        assert timeframe == "1H"
        self.history_limits.append(limit)
        return self.rows[-limit:], 0

    def get(self, path, params=None):
        self.paths.append((path, dict(params or {})))
        assert path == "/api/v5/market/candles"
        assert params["bar"] == "1m"
        return self.minutes


class FakeQuoteSource:
    def __init__(self, client, symbols=("BTC-USDT-SWAP",)):
        self.client = client
        self.symbols = symbols
        self.listeners = []
        self.lock = threading.RLock()
        self.connected = True
        self.last_message_ms = client.clock()
        self.stream_error = None


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self._payload


class TestMonthWindow:
    def test_first_complete_week_is_calendar_based_and_dst_aware(self):
        spring = month_window(ms(2026, 3, 8, 12))
        assert spring["first_monday"] == "2026-03-02"
        assert spring["expected_1h_bars"] == 167
        assert spring["ready_ms"] - spring["start_ms"] == 167 * HOUR_MS

        february = month_window(ms(2026, 2, 1, 12))
        assert february["first_monday"] == "2026-02-02"
        assert datetime.fromtimestamp(february["ready_ms"] / 1000, UTC).date() == date(2026, 2, 9)
        assert february["expires_ms"] == ms(2026, 3, 1, 5)

        november = month_window(ms(2026, 11, 11))
        assert november["first_monday"] == "2026-11-02"
        assert november["expected_1h_bars"] == 168
        assert november["expires_ms"] == ms(2026, 12, 1, 5)

    def test_anchor_requires_every_completed_source_hour_and_never_reads_future(self):
        window = month_window(ms(2026, 3, 9, 5))
        expected = expected_hour_opens(window)
        rows = [{"t": stamp, "h": 110.0 if stamp < window["ready_ms"] else 999.0,
                 "l": 90.0 if stamp < window["ready_ms"] else 1.0} for stamp in expected]
        developing = make_anchor(rows, window, window["ready_ms"] - 1)
        assert developing["state"] == "developing"
        assert developing["high"] is None

        complete = make_anchor(rows, window, window["ready_ms"])
        assert complete["state"] == "ready"
        assert complete["high"] == 110.0
        assert complete["low"] == 90.0
        assert complete["upper"] == 110.0 + 0.618 * 20.0
        assert complete["lower"] == 90.0 - 0.618 * 20.0

        missing = [row for row in rows if row["t"] != expected[40]]
        assert make_anchor(missing, window, window["ready_ms"])["state"] == "incomplete"

    def test_history_recovery_returns_first_closed_hour_once_per_side(self):
        window = month_window(ms(2026, 11, 11))
        anchor = {**window, "state": "ready", "upper": 110.0, "lower": 90.0}
        rows = [
            {"t": window["ready_ms"], "h": 111.0, "l": 89.0},
            {"t": window["ready_ms"] + HOUR_MS, "h": 130.0, "l": 70.0},
        ]
        touches = closed_hour_touches(rows, anchor, window["ready_ms"] + 2 * HOUR_MS)
        assert {event["side"]: event["candle_ms"] for event in touches} == {
            "upper": window["ready_ms"], "lower": window["ready_ms"]}


class TestMonthFirstWeekBook:
    def test_persistent_event_identity_cursor_and_concurrent_dedup(self, tmp_path):
        now = ms(2026, 11, 11, 12, 0)
        window = month_window(now)
        book = MonthFirstWeekBook(tmp_path)

        def insert(_):
            return book.touch(month=window["month"], symbol="BTC-USDT-SWAP", side="upper",
                              level=110.0, price=110.0, candle_extreme=112.0,
                              occurred_ms=now, candle_ms=now // MINUTE_MS * MINUTE_MS,
                              created_ms=now, expires_ms=window["expires_ms"],
                              source="live", candle_confirmed=False)

        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(insert, range(16)))
        assert sum(row is not None for row in rows) == 1
        first = next(row for row in rows if row is not None)
        assert first["event_id"] == "2026-11|BTC-USDT-SWAP|upper"
        assert first["event_type"] == EVENT_TYPE
        assert first["touch_confirmed"] is True
        assert first["candle_confirmed"] is False

        # The other side has an independent first-touch identity.
        second = book.touch(month=window["month"], symbol="BTC-USDT-SWAP", side="lower",
                            level=80.0, price=80.0, candle_extreme=78.0,
                            occurred_ms=now + 1, candle_ms=now // MINUTE_MS * MINUTE_MS,
                            created_ms=now + 1, expires_ms=window["expires_ms"],
                            source="live", candle_confirmed=False)
        assert second["event_id"].endswith("|lower")

        reopened = MonthFirstWeekBook(tmp_path)
        all_rows = reopened.events_after(0, 500)
        assert [row["event_id"] for row in all_rows] == [first["event_id"], second["event_id"]]
        assert reopened.events_after(all_rows[0]["id"], 500) == [all_rows[1]]

    def test_history_touch_never_creates_notification_outbox(self, tmp_path):
        now = ms(2026, 11, 11, 12)
        window = month_window(now)
        book = MonthFirstWeekBook(tmp_path)
        book.set_enabled("telegram", True, now - 1000)
        book.touch(month=window["month"], symbol="ETH-USDT-SWAP", side="upper",
                   level=110.0, price=110.0, candle_extreme=112.0,
                   occurred_ms=now - 60 * MINUTE_MS, candle_ms=now - 60 * MINUTE_MS,
                   created_ms=now, expires_ms=window["expires_ms"],
                   source="history", candle_confirmed=True)
        assert book.pending_notice("telegram", now) is None
        assert book.channel_status("telegram")["counts"] == {}


class TestMonthFirstWeekService:
    def _service(self, tmp_path, *, now, prefix_high=98.0, prefix_low=92.0, include_open=True, sender=None):
        window = month_window(now)
        rows = source_rows(window, now)
        minutes = raw_minute_rows(now, high=prefix_high, low=prefix_low)
        client = FakeClient(now, rows, minutes)
        client.include_open = include_open
        source = FakeQuoteSource(client)
        service = MonthFirstWeek1618Service(tmp_path, quote_source=source, client=client, sender=sender)
        service._load_month(window)
        service._subscribed_ms = now - 60 * MINUTE_MS
        return service, source, client, window

    def test_live_touch_is_queued_on_1m_update_without_waiting_for_hour_close(self, tmp_path):
        now = ms(2026, 11, 11, 12, 34, 20)
        sent = []
        response = FakeResponse({"ok": True, "result": {"message_id": 771}})
        service, source, client, window = self._service(tmp_path, now=now, sender=lambda *a, **k: sent.append((a, k)) or response)
        service.book.set_enabled("telegram", True, now - 1000)

        # No touch in the current-hour prefix; the completed weekly anchor arms both sides.
        service.on_quote("BTC-USDT-SWAP", {"candle_ms": now // MINUTE_MS * MINUTE_MS,
            "received_ms": now, "high": 98.0, "low": 92.0, "price": 95.0,
            "candle_confirmed": False})
        service.step(now)
        item = service.snapshot()["items"][0]
        assert item["state"] == "ready"
        assert item["armed_sides"] == ["lower", "upper"]

        live = {"candle_ms": now // MINUTE_MS * MINUTE_MS, "received_ms": now + 1000,
                "high": item["upper"] + 0.1, "low": 95.0, "price": 100.0,
                "candle_confirmed": False}
        client.now_ms = now + 1000
        service.on_quote("BTC-USDT-SWAP", live)
        # The websocket listener has only queued memory state; persistence is worker-owned.
        assert service.book.events(window["month"]) == []
        with patch("yoyo.monitor.month_first_week_1618_service.telegram_credentials",
                   return_value=("test-token", "test-chat")):
            service.step(now + 1000, deliver=True)

        event = service.book.events(window["month"])[0]
        assert event["side"] == "upper"
        assert event["source"] == "live"
        assert event["touch_confirmed"] is True
        assert event["candle_confirmed"] is False
        assert service.book.channel_status("telegram")["counts"] == {"sent": 1}
        assert len(sent) == 1
        assert "OKX%3ABTCUSDT.P" in sent[0][1]["json"]["reply_markup"]["inline_keyboard"][0][0]["url"]
        assert "月首周 1.618" in sent[0][1]["json"]["text"].splitlines()[0]
        assert service.events_after(0)[0]["event_id"] == "2026-11|BTC-USDT-SWAP|upper"

    def test_boot_current_minute_touch_is_history_not_a_fresh_notification(self, tmp_path):
        now = ms(2026, 11, 11, 12, 34, 20)
        window = month_window(now)
        upper = 100 + 0.618 * 10
        service, source, client, window = self._service(tmp_path, now=now,
            prefix_high=upper + 1.0, prefix_low=95.0)
        service._subscribed_ms = now
        service.book.set_enabled("telegram", True, now - 1000)
        # A current-minute partial OHLC can include a wick from before subscribe.
        # Its candle open predates the post-subscribe live watermark, so it is history.
        service.on_quote("BTC-USDT-SWAP", {"candle_ms": now // MINUTE_MS * MINUTE_MS,
            "received_ms": now, "high": upper + 1.0, "low": 95.0, "price": 100.0,
            "candle_confirmed": False})
        service.step(now)
        event = service.book.events(window["month"])[0]
        assert event["source"] == "history"
        assert service.book.pending_notice("telegram", now) is None
        assert service.snapshot()["items"][0]["armed_sides"] == ["lower"]

    def test_current_hour_prefix_gap_is_recovering_and_does_not_arm(self, tmp_path):
        now = ms(2026, 11, 11, 12, 34, 20)
        service, _, _, window = self._service(tmp_path, now=now, include_open=False)
        service.step(now)
        item = service.snapshot()["items"][0]
        assert item["state"] == "recovering"
        assert item["armed_sides"] == []
        assert item["recovery_error"] == "RuntimeError"

    def test_snapshot_and_exclusive_event_cursor_routes(self, tmp_path):
        now = ms(2026, 11, 11, 12)
        service, _, _, window = self._service(tmp_path, now=now)
        service.book.touch(month=window["month"], symbol="ETH-USDT-SWAP", side="upper",
                           level=110.0, price=110.0, candle_extreme=111.0,
                           occurred_ms=now - 60 * MINUTE_MS, candle_ms=now - 60 * MINUTE_MS,
                           created_ms=now, expires_ms=window["expires_ms"],
                           source="history", candle_confirmed=True)
        service.book.touch(month=window["month"], symbol="BTC-USDT-SWAP", side="lower",
                           level=80.0, price=79.0, candle_extreme=78.0,
                           occurred_ms=now - 30 * MINUTE_MS, candle_ms=now - 30 * MINUTE_MS,
                           created_ms=now, expires_ms=window["expires_ms"],
                           source="live", candle_confirmed=False)
        app = FastAPI()
        install(app, service)
        routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "path")}
        status = routes["/api/month-first-week-1618"]()
        assert status["schema"] == "month_first_week_1618.v1"
        assert status["month"] == "2026-11"
        assert [event["event_id"] for event in status["touches"]] == [
            "2026-11|BTC-USDT-SWAP|lower", "2026-11|ETH-USDT-SWAP|upper"]
        assert all(event["type"] == EVENT_TYPE and event["event_type"] == EVENT_TYPE
                   for event in status["touches"])
        assert all(valid_month_touch(event) for event in status["touches"])
        assert status["touches"][0]["channels"] == {}
        page = routes["/api/month-first-week-1618/events"](after_id=0, limit=500)
        assert page["events"][0]["event_id"] == "2026-11|ETH-USDT-SWAP|upper"
        assert all(event["type"] == EVENT_TYPE and valid_month_touch(event) for event in page["events"])
        latest = page["latest_id"]
        assert routes["/api/month-first-week-1618/events"](after_id=latest, limit=500)["events"] == []
