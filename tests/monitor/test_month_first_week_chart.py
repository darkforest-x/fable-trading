from types import SimpleNamespace

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
    install(app, book)
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
