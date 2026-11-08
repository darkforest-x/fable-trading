"""Published Binance USD-M funding for the simulated instruments only."""
import math
from urllib.parse import urlencode

BASE = "https://fapi.binance.com/fapi/v1"
BAR = 300_000
HOUR = 3_600_000


def funding_at(symbols, boundary_ms, read):
    """Return actual settlement rate × mark on a UTC 5m boundary.

    Binance timestamps can be milliseconds past the nominal hour. A missing
    scheduled settlement or an unavailable response must defer accounting.
    """
    names = sorted(set(symbols))
    if not names:
        return [], {}
    params = {"startTime": boundary_ms, "endTime": boundary_ms + BAR - 1, "limit": 1000}
    pending = {s: {"time_ms": boundary_ms, "due": True, "available": False} for s in names}
    # Public funding-rate history returns all contracts when symbol is omitted.
    rows = read(BASE + "/fundingRate?" + urlencode(params))
    if not isinstance(rows, list):
        raise ValueError("Invalid public funding history")
    grouped = {s: [] for s in names}
    for row in rows:
        if row.get("symbol") in grouped:
            grouped[row["symbol"]].append(row)
    intervals = {}
    if boundary_ms % HOUR == 0 and any(not group for group in grouped.values()):
        info = read(BASE + "/fundingInfo")
        if not isinstance(info, list):
            raise ValueError("Invalid public funding schedule")
        intervals = {r["symbol"]: r for r in info if r.get("symbol") in grouped}
    actual = []
    for symbol, group in grouped.items():
        if not group and len(rows) == params["limit"]:
            group = read(BASE + "/fundingRate?" + urlencode(dict(params, symbol=symbol)))
        if not group:
            item = intervals.get(symbol, {})
            interval = int(item.get("fundingIntervalHours", 8)) * HOUR
            if interval <= 0:
                raise ValueError("Invalid funding interval for " + symbol)
            due = boundary_ms % interval == 0
            if due and item.get("updateTime", 0) > boundary_ms:
                # A later schedule revision cannot establish that an earlier
                # empty historical interval was due. A later published settlement
                # confirms that the historical query is already past the boundary.
                later = read(BASE + "/fundingRate?" + urlencode({"symbol": symbol, "startTime": boundary_ms + BAR, "limit": 1}))
                due = not bool(later)
            pending[symbol].update(due=due, available=not due)
            continue
        if len(group) != 1:
            raise ValueError("Ambiguous settlement for " + symbol)
        row = group[0]
        actual_ms = int(row["fundingTime"])
        if row["symbol"] != symbol or actual_ms // BAR * BAR != boundary_ms:
            raise ValueError("Funding outside requested symbol/window")
        try:
            rate, mark = float(row["fundingRate"]), float(row["markPrice"])
        except (ValueError, TypeError, KeyError):
            continue
        if not math.isfinite(rate) or not math.isfinite(mark) or mark <= 0:
            continue
        event = {"symbol": symbol, "time_ms": boundary_ms, "exchange_ms": actual_ms,
                 "rate": rate, "mark_price": mark, "available": True, "source": "binance_fapi_fundingRate"}
        pending[symbol].update(available=True, rate=rate, mark_price=mark)
        actual.append(event)
    return actual, pending
