"""Demand-only monthly monitor charts using the chart server's shared OKX reads."""
from typing import Optional

from fastapi import HTTPException, Query

from .chart_periods import open_time, step_ms
from .month_first_week_1618 import HOUR_MS, MINUTE_MS, SYMBOL_RE, month_window

LEVELS = ("high", "low", "upper", "lower")
BOUNDS = ("start_ms", "ready_ms", "expires_ms")


def _marker(event, bars, period, start, end):
    """Use the stored candle, then require an uninterrupted prefix up to the first crossing."""
    step = step_ms(period)
    by_time = {bar["time"]: bar for bar in bars}
    upper = event["side"] == "upper"
    for stamp in range(open_time(start, period), end, step):
        candle = by_time.get(stamp)
        if candle is None:
            break
        reached = candle["high"] >= event["level"] if upper else candle["low"] <= event["level"]
        if reached:
            return stamp
    return None


def chart(book, history, event_id, period):
    event = book.chart_record(event_id)
    if event is None:
        raise HTTPException(404, "未找到这条月首周触价")
    anchor = {k: event[k] for k in BOUNDS + LEVELS}
    if any(v is None for v in anchor.values()):
        raise HTTPException(503, "本次触价的首周区间暂不可用")

    # Closed-hour recovery saves the hour open. Current-hour recovery (including
    # minute 00) and WebSocket touches both save the originating minute open.
    start = event["candle_ms"]
    from_hour = (event["source"] == "history" and start % HOUR_MS == 0
                 and event["created_ms"] >= start + HOUR_MS)
    end = start + (HOUR_MS if from_hour else MINUTE_MS)
    step = step_ms(period)
    now = history.candles.client.clock()
    left = open_time(start, period) - 150 * step
    if step >= HOUR_MS:
        left = min(left, open_time(anchor["start_ms"], period))
    right = open_time(end - 1, period) + 81 * step
    symbol = event["symbol"]
    candles, forming = _window(history, symbol, period, left, right, now)
    return dict(event_id=event_id, symbol=symbol, month=event["month"], side=event["side"],
                source=event["source"], level=event["level"], bar=period, anchor=anchor,
                candles=candles, forming_time_ms=forming, observation_start_ms=start,
                observation_end_ms=end, marker_ms=_marker(event, candles, period, start, end))


def market_chart(service, history, symbol, period):
    symbols = service.quote_source.symbols
    if not symbols:
        raise HTTPException(503, "监控合约列表正在更新")
    if symbol not in symbols:
        raise HTTPException(404, "此合约不在月首周监控中")
    now = history.candles.client.clock()
    window = month_window(now)
    record = service.book.anchor(window["month"], symbol) or {}
    anchor = {k: window[k] for k in BOUNDS}
    anchor.update({k: record.get(k) if window["complete"] else None for k in LEVELS})
    step = step_ms(period)
    current = open_time(now, period)
    left = current - 220 * step
    if step >= HOUR_MS:
        left = min(left, open_time(window["start_ms"], period))
    candles, forming = _window(history, symbol, period, left, current + step, now)
    return dict(event_id=None, symbol=symbol, month=window["month"], side=None, source=None,
                level=None, bar=period, anchor=anchor, candles=candles, forming_time_ms=forming,
                observation_start_ms=None, observation_end_ms=None, marker_ms=None)


def _window(history, symbol, period, left, right, now):
    current = open_time(now, period)
    cursor = min(right, current)
    bars = {}
    # Some instruments return shorter pages. Keep the event in the requested
    # window even when the first page contains only post-touch candles.
    for _ in range(4):
        page = history.read(symbol, period, cursor)
        if page["state"] == "retry":
            raise HTTPException(503, page["error"])
        bars.update((row["time"], row) for row in page["candles"] if left <= row["time"] < right)
        older = page["next_before_ms"]
        if not page["has_more"] or older <= left or older >= cursor:
            break
        cursor = older
    forming = None
    if right > current:
        tail = history.candles.recent(symbol, period)
        for row in tail["closed_candles"]:
            if left <= row["time"] < right:
                bars[row["time"]] = row
        row = tail["forming_candle"]
        if row and left <= row["time"] < right and row["time"] not in bars:
            forming = row["time"]
            bars[forming] = row
    return [bars[t] for t in sorted(bars)], forming


def install(app, service):
    @app.get("/api/month-first-week-1618/chart")
    def month_chart(event_id: Optional[str] = Query(None, min_length=1, max_length=96),
                    symbol: Optional[str] = Query(None, pattern=SYMBOL_RE.pattern),
                    period: str = Query("1H", pattern=r"^(1m|5m|15m|1H|4H|12H|1D)$")):
        if bool(event_id) == bool(symbol):
            raise HTTPException(422, "请选择一条触价或一个合约")
        if event_id:
            return chart(service.book, app.state.monday_chart_history, event_id, period)
        return market_chart(service, app.state.monday_chart_history, symbol, period)
