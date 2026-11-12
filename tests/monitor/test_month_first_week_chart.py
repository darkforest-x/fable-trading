from types import SimpleNamespace
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from yoyo.monitor.chart_periods import step_ms
from yoyo.monitor.month_first_week_1618 import MonthFirstWeekBook, HOUR_MS, MINUTE_MS
from yoyo.monitor.month_first_week_chart import install


START = 1_794_312_000_000 // HOUR_MS * HOUR_MS
SYMBOL = "AAA-USDT-SWAP"
EVENT = "2026-11|" + SYMBOL + "|lower"


def candle(stamp, low=94.0):
    return dict(time=stamp, open=96.0, high=97.0, low=low, close=96.0, volume=1.0)


class History:
    def __init__(self, now, bars, forming=None):
        self.candles = SimpleNamespace(client=SimpleNamespace(clock=lambda: now),
                                       recent=lambda *_: dict(closed_candles=[], forming_candle=forming))
        self.bars = bars
        self.levels = SimpleNamespace(read_display=lambda symbol, at: dict(
            state="ready", symbol=symbol, asof=at))

    def read(self, symbol, period, before):
        assert symbol == SYMBOL
        rows = [r for r in self.bars if r["time"] < before][-80:]
        return dict(state="ready" if rows else "exhausted", candles=rows, has_more=bool(rows),
                    next_before_ms=rows[0]["time"] if rows else before)


def setup_chart(tmp_path, history, *, source, created):
    book = MonthFirstWeekBook(tmp_path)
    anchor = dict(month="2026-11", start_ms=START - 8*24*HOUR_MS, ready_ms=START - 24*HOUR_MS,
                  expires_ms=START + 20*24*HOUR_MS, high=100, low=95, upper=103.09, lower=91.91)
    book.save_anchor(anchor, SYMBOL, created, state="ready")
    book.touch(month=anchor["month"], symbol=SYMBOL, side="lower", level=anchor["lower"],
               price=anchor["lower"], candle_extreme=90, occurred_ms=START, candle_ms=START,
               created_ms=created, expires_ms=anchor["expires_ms"], source=source, candle_confirmed=True)
    app = FastAPI()
    app.state.monday_chart_history = history
    monday_book = SimpleNamespace(records=lambda week: [], events=lambda month: [])
    install(app, SimpleNamespace(book=book, quote_source=SimpleNamespace(symbols=[SYMBOL], book=monday_book)))
    return TestClient(app)


def test_historical_hour_finds_first_minute_across_pages_and_requires_complete_prefix(tmp_path):
    bars = [candle(START + i*MINUTE_MS, low=90 if i in (37, 49) else 94) for i in range(-150, 140)]
    history = History(START + 40*24*HOUR_MS, bars)
    http = setup_chart(tmp_path, history, source="history", created=START + 3*HOUR_MS)
    endpoint = "/api/month-first-week-1618/chart"
    params = dict(event_id=EVENT, period="1m")
    result = http.get(endpoint, params=params).json()
    assert result["observation_end_ms"] == START + HOUR_MS
    assert result["marker_ms"] == START + 37*MINUTE_MS
    assert result["anchor"]["lower"] == result["level"] == 91.91
    assert result["weekly_ranges"] == [dict(state="ready", symbol=SYMBOL, asof=START)]
    assert result["candles"][0]["time"] == START - 150*MINUTE_MS
    assert result["forming_time_ms"] is None
    history.bars = [r for r in bars if r["time"] != START + 5*MINUTE_MS]
    assert http.get(endpoint, params=params).json()["marker_ms"] is None
    assert http.get(endpoint, params=dict(event_id=EVENT.replace("lower", "upper"))).status_code == 404
    assert http.get(endpoint, params=dict(event_id=EVENT, period="garbage")).status_code == 422


def test_minute_recovery_at_top_of_hour_and_live_unclosed_bar(tmp_path):
    history = History(START + 20_000, [candle(START - MINUTE_MS)], forming=candle(START, low=90))
    http = setup_chart(tmp_path, history, source="history", created=START + 11_000)
    result = http.get("/api/month-first-week-1618/chart", params=dict(event_id=EVENT, period="1m")).json()
    assert result["observation_end_ms"] == START + step_ms("1m")
    assert result["marker_ms"] == result["forming_time_ms"] == START
    assert len(result["candles"]) == 2


def test_untouched_contract_can_open_during_new_month_without_previous_levels(tmp_path):
    new_month = int(datetime(2026, 12, 1, 12, tzinfo=timezone.utc).timestamp() * 1000)
    history = History(new_month, [], forming=candle(new_month))
    http = setup_chart(tmp_path, history, source="live", created=START)
    path = "/api/month-first-week-1618/chart"
    current = http.get(path, params=dict(symbol=SYMBOL)).json()
    assert current["month"] == "2026-12"
    assert current["event_id"] is None and current["marker_ms"] is None
    assert current["anchor"]["upper"] is None
    assert current["forming_time_ms"] == current["candles"][0]["time"] == new_month
    assert http.get(path, params=dict(symbol="UNKNOWN-USDT-SWAP")).status_code == 404
    assert http.get(path, params=dict(symbol=SYMBOL, event_id=EVENT)).status_code == 422


def test_daily_color_matches_ny_first_week_across_months_and_dst(tmp_path):
    def utc(month, day):
        return int(datetime(2026, month, day, tzinfo=timezone.utc).timestamp() * 1000)

    days = [(10, 4), (10, 5), (10, 11), (10, 12), (11, 1), (11, 2), (11, 8), (11, 9)]
    history = History(utc(11, 12), [candle(utc(*day)) for day in days])
    http = setup_chart(tmp_path, history, source="live", created=START)
    data = http.get("/api/month-first-week-1618/chart", params=dict(symbol=SYMBOL, period="1D")).json()
    assert [row["first_week"] for row in data["candles"]] == [False, True, True, False]*2


def test_level_overlay_uses_only_frozen_prices_for_selected_contract(tmp_path):
    history = History(START, [])
    http = setup_chart(tmp_path, history, source="live", created=START)
    book = MonthFirstWeekBook(tmp_path)
    november = book.anchor("2026-11", SYMBOL)
    past = {**november, "month": "2026-10", "start_ms": START-40*24*HOUR_MS,
            "ready_ms": START-33*24*HOUR_MS, "expires_ms": START-10*24*HOUR_MS}
    book.save_anchor(past, SYMBOL, START, state="recovering")
    future = {**november, "month": "2026-12", "ready_ms": START+30*24*HOUR_MS}
    book.save_anchor(future, SYMBOL, START)
    http_path = "/api/month-first-week-1618/levels"
    result = http.get(http_path, params={"symbol": SYMBOL}).json()
    assert [r["month"] for r in result["ranges"]] == ["2026-10", "2026-11"]
    assert result["symbol"] == SYMBOL and result["ranges"][0]["expires_ms"] == past["expires_ms"]
    assert http.get(http_path, params={"symbol": "OTHER-USDT-SWAP"}).json()["ranges"] == []
    assert "/api/touch-reclaims" in {route.path for route in http.app.routes}
    history.levels.read_display = lambda *_: dict(state="missing_monday")
    event = http.get("/api/month-first-week-1618/chart", params=dict(event_id=EVENT, period="30m"))
    assert event.status_code == 200 and event.json()["weekly_ranges"] == []
