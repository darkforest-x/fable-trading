from datetime import datetime, timezone
from threading import Event, Lock
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from yoyo.monitor.chart_periods import open_time, step_ms
from yoyo.monitor.month_first_week_1618 import HOUR_MS
from yoyo.monitor.touch_reclaims import TouchReclaimReader, install


def ms(year, month, day, hour=0, minute=0):
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


SYMBOL = "AAA-USDT-SWAP"
TOUCH = ms(2026, 11, 10, 3, 30)


def monday_event(side="upper", *, symbol=SYMBOL, touch=TOUCH):
    return {"week": "2026-11-09", "symbol": symbol, "side": side, "level": 100.0,
            "price": 100.0, "source": "live", "candle_ms": touch,
            "observed_ms": touch + 5_000}


def month_event(side="lower", *, symbol=SYMBOL, touch=TOUCH):
    return {"month": "2026-11", "symbol": symbol, "side": side, "level": 100.0,
            "price": 100.0, "source": "live", "candle_ms": touch,
            "occurred_ms": touch + 5_000}


class FakeHistory:
    def __init__(self, now_ms, rows=None, *, block=False):
        client = SimpleNamespace(clock=lambda: now_ms)
        client.get = self.get
        self.candles = SimpleNamespace(client=client)
        self.rows = rows or {}
        self.calls = []
        self.lock = Lock()
        self.started = Event()
        self.release = Event()
        if not block:
            self.release.set()

    def get(self, path, params):
        assert path == "/api/v5/market/history-candles"
        symbol, native = params["instId"], params["bar"]
        period = "12H" if native == "12Hutc" else native
        count, after = int(params["limit"]), int(params["after"])
        with self.lock:
            self.calls.append((symbol, native, count, after))
        self.started.set()
        self.release.wait(5)
        step = step_ms(period)
        first = after - count * step
        rows = sorted((row for row in self.rows.get((symbol, period), [])
                       if first <= row["time"] < after), key=lambda row: row["time"], reverse=True)
        return [[str(row["time"]), str(row["open"]), str(row["high"]), str(row["low"]),
                 str(row["close"]), str(row["volume"]), str(row["volume"]), str(row["volume"]), "1"]
                for row in rows]


class FakeBook:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def records(self, week):
        self.calls.append(("week", week))
        return [dict(row) for row in self.rows if row["week"] == week]

    def events(self, month):
        self.calls.append(("month", month))
        return [dict(row) for row in self.rows if row["month"] == month]


def bar(period, stamp, close):
    return {"time": stamp, "open": close, "high": max(close, 101.0),
            "low": min(close, 99.0), "close": close, "volume": 1.0}


def make_app(history, monday_rows, month_rows):
    app = FastAPI()
    reader = install(app, monday_book=FakeBook(monday_rows), month_book=FakeBook(month_rows),
                     history=history)
    return TestClient(app), reader


def expected_open(event, period):
    touch = event.get("candle_ms", event.get("occurred_ms", event.get("observed_ms")))
    return open_time(touch, period)


def test_batch_routes_return_period_scoped_touch_facts_and_cursor_pages():
    now = ms(2026, 11, 10, 0, 5)
    rows = [monday_event("upper"), monday_event("lower", symbol="BBB-USDT-SWAP")]
    month_rows = [month_event("lower")]
    client, reader = make_app(FakeHistory(now), rows, month_rows)
    try:
        first = client.get("/api/touch-reclaims?kind=monday&week=2026-11-09&limit=1").json()
        assert first["schema"] == "touch_reclaims.v1"
        assert first["kind"] == "monday" and first["week"] == first["period"] == "2026-11-09"
        assert first["count"] == 1 and first["has_more"] is first["truncated"] is True
        assert first["items"][0]["event_id"] == first["next_cursor"]
        assert {"level", "side", "candle_ms", "observed_ms"} <= first["items"][0].keys()
        assert set(first["items"][0]["reclaims"]) == {"1H", "4H", "12H"}
        assert all(item["status"] == "pending" for item in first["items"][0]["reclaims"].values())

        second = client.get("/api/touch-reclaims", params={"kind": "monday", "week": "2026-11-09",
                                                            "limit": 1, "after": first["next_cursor"]}).json()
        assert second["count"] == 1 and second["has_more"] is False
        monthly = client.get("/api/touch-reclaims?kind=month_first_week&month=2026-11").json()
        assert monthly["kind"] == "month_first_week"
        assert monthly["month"] == monthly["period"] == "2026-11"
        assert [item["event_id"] for item in monthly["items"]] == ["2026-11|AAA-USDT-SWAP|lower"]
        assert client.get("/api/touch-reclaims?kind=monday&week=2026-11-10").status_code == 422
        assert client.get("/api/touch-reclaims?kind=month_first_week&week=2026-11-09").status_code == 422
    finally:
        reader.close()


def test_batch_is_nonblocking_deduplicated_and_uses_strict_closed_bar_reclaim():
    monday_upper = monday_event("upper")
    monday_lower = monday_event("lower", touch=ms(2026, 11, 10, 15, 30))
    month_lower = month_event("lower")
    now = ms(2026, 11, 12, 12, 0)
    rows = {}
    for event, closes in ((monday_upper, {"1H": 99.0, "4H": 100.0, "12H": 101.0}),
                          (monday_lower, {"1H": 101.0, "4H": 100.0, "12H": 99.0})):
        for period, close in closes.items():
            stamp = expected_open(event, period)
            rows.setdefault((SYMBOL, period), []).append(bar(period, stamp, close))
    history = FakeHistory(now, rows, block=True)
    client, reader = make_app(history, [monday_upper, monday_lower], [month_lower])
    try:
        response = client.get("/api/touch-reclaims?kind=monday&week=2026-11-09")
        assert response.status_code == 200
        assert history.started.wait(1)  # The fetch is happening off the request thread.
        initial = response.json()
        assert all(item["status"] == "pending" for row in initial["items"]
                   for item in row["reclaims"].values())
        calls_while_blocked = len(history.calls)
        client.get("/api/touch-reclaims?kind=monday&week=2026-11-09")
        assert len(history.calls) == calls_while_blocked

        history.release.set()
        assert reader.wait_for_idle(2)
        settled = client.get("/api/touch-reclaims?kind=monday&week=2026-11-09").json()
        by_side = {row["side"]: row["reclaims"] for row in settled["items"]}
        assert [by_side["upper"][period]["status"] for period in ("1H", "4H", "12H")] == [
            "reclaimed", "not_reclaimed", "not_reclaimed"]
        assert [by_side["lower"][period]["status"] for period in ("1H", "4H", "12H")] == [
            "reclaimed", "not_reclaimed", "not_reclaimed"]
        assert by_side["upper"]["4H"]["close"] == 100.0  # Equality is not a return inside.
        assert len(history.calls) == 3  # One grouped read per symbol/period, not per event.
        assert all(limit < 300 for _, _, limit, _ in history.calls)

        # The same closed target candle is reused by the other touch ledger.
        client.get("/api/touch-reclaims?kind=month_first_week&month=2026-11")
        assert len(history.calls) == 3
    finally:
        history.release.set()
        reader.close()


def test_missing_closed_candle_is_unavailable_not_not_reclaimed():
    event = monday_event("upper")
    now = ms(2026, 11, 12, 12, 0)
    # Other bars prove the target period was read; the exact touch-aligned bar is absent.
    rows = {("AAA-USDT-SWAP", "1H"): [bar("1H", expected_open(event, "1H") - HOUR_MS, 105.0)]}
    history = FakeHistory(now, rows, block=True)
    client, reader = make_app(history, [event], [])
    try:
        pending = client.get("/api/touch-reclaims?kind=monday&week=2026-11-09").json()
        assert history.started.wait(1)
        assert all(item["status"] == "pending" for item in pending["items"][0]["reclaims"].values())
        history.release.set()
        assert reader.wait_for_idle(2)
        settled = client.get("/api/touch-reclaims?kind=monday&week=2026-11-09").json()["items"][0]["reclaims"]
        assert settled["1H"]["status"] == "unavailable"
        assert settled["1H"]["reason"] == "closed_candle_missing"
        assert settled["1H"]["close"] is None
        assert settled["1H"]["status"] != "not_reclaimed"
    finally:
        history.release.set()
        reader.close()


def test_future_periods_remain_pending_without_market_reads():
    event = monday_event("upper", touch=ms(2026, 11, 12, 11, 30))
    history = FakeHistory(ms(2026, 11, 12, 11, 40))
    client, reader = make_app(history, [event], [])
    try:
        result = client.get("/api/touch-reclaims?kind=monday&week=2026-11-09").json()["items"][0]["reclaims"]
        assert all(item["status"] == "pending" for item in result.values())
        assert history.calls == []
    finally:
        reader.close()


def test_touch_candle_remains_authoritative_when_websocket_observation_crosses_hour():
    event = monday_event("upper", touch=ms(2026, 11, 12, 12, 59))
    event["observed_ms"] = ms(2026, 11, 12, 13, 0) + 10_000
    target = TouchReclaimReader._target(event, "1H")
    assert target == (ms(2026, 11, 12, 12), ms(2026, 11, 12, 13))
