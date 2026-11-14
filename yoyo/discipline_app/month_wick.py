"""Resolve the monthly first-touch 1m search interval from monitor state."""
import math
import re
from collections.abc import Mapping

from yoyo.monitor.month_first_week_1618 import HOUR_MS, MINUTE_MS, month_window

from .monitor_feed import MonitorUnavailable

_SYMBOL = re.compile(r"^[A-Z0-9]{1,40}-USDT-SWAP$")


def _unavailable():
    raise MonitorUnavailable("月首周首次触价止损数据暂不可用，请稍后重试或手动填写")


def month_wick_anchor(monitor, symbol, side, now_ms):
    """Return ``(month, level, start_ms, end_ms)`` for 1m opens in ``[start,end)``.

    ``side`` is trade direction; long maps to lower, short to upper. A forming
    minute may be in the range; the caller handles the closed-candle boundary.
    The complete top-level ``touches`` list has one persisted event per month,
    symbol, and edge, keyed by ``month|symbol|edge``.
    """
    if (type(now_ms) is not int or now_ms <= 0 or not isinstance(symbol, str)
            or not _SYMBOL.fullmatch(symbol) or side not in {"long", "short"}):
        _unavailable()
    raw = monitor.month_first_week_1618()
    if not isinstance(raw, Mapping):
        _unavailable()
    window = month_window(now_ms)
    snap_now, bounds = raw.get("now_ms"), raw.get("window")
    if (raw.get("schema") != "month_first_week_1618.v1" or raw.get("timezone") != "America/New_York"
            or raw.get("month") != window["month"] or type(snap_now) is not int
            or abs(snap_now - now_ms) > 60_000 or not isinstance(bounds, Mapping)):
        _unavailable()
    keys = ("month", "first_monday", "start_ms", "ready_ms", "expires_ms")
    if (any(bounds.get(key) != window[key] for key in keys) or bounds.get("complete") is not True
            or not window["complete"] or not window["ready_ms"] <= now_ms < window["expires_ms"]):
        _unavailable()

    edge = "lower" if side == "long" else "upper"
    period_key = window["month"]
    event_id = f"{period_key}|{symbol}|{edge}"
    events = raw.get("touches")
    if not isinstance(events, list):
        _unavailable()
    found = [event for event in events if isinstance(event, Mapping) and event.get("event_id") == event_id]
    if len(found) != 1:
        _unavailable()
    event = found[0]
    level = event.get("level")
    source = event.get("source")
    candle_ms, occurred_ms, created_ms = (event.get(key) for key in ("candle_ms", "occurred_ms", "created_ms"))
    if (event.get("type") != "month_first_week_touch" or event.get("month") != period_key
            or event.get("symbol") != symbol or event.get("side") != edge
            or source not in {"live", "history"} or event.get("touch_confirmed") is not True
            or isinstance(level, bool) or not isinstance(level, (int, float))
            or not math.isfinite(level) or level <= 0
            or any(type(value) is not int or value <= 0 for value in
                   (candle_ms, occurred_ms, created_ms, event.get("expires_ms")))
            or event["expires_ms"] != window["expires_ms"]
            or not window["ready_ms"] <= candle_ms < window["expires_ms"]
            or candle_ms % MINUTE_MS or occurred_ms < candle_ms
            or max(occurred_ms, created_ms) > max(now_ms, snap_now)
            or created_ms < occurred_ms or (source == "history" and occurred_ms != candle_ms)):
        _unavailable()

    # Match month_first_week_chart: closed-H1 history is created after its hour;
    # live and targeted current-hour recovery preserve the exact 1m open.
    from_hour = (source == "history" and candle_ms % HOUR_MS == 0
                 and created_ms >= candle_ms + HOUR_MS)
    start_ms = candle_ms
    end_ms = min(candle_ms + (HOUR_MS if from_hour else MINUTE_MS), window["expires_ms"])
    if end_ms <= start_ms:
        _unavailable()
    return period_key, float(level), start_ms, end_ms
