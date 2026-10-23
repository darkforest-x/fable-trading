"""Pure Monday wick selection shared by web and mobile order defaults.

Reads only OHLC of closed bars after this NY Monday range finished. Candle
session boundaries belong to the caller (mobile daily bars use OKX UTC+8).
The input level must lie in the wick, never only in the candle body.
"""
from decimal import Decimal
from yoyo.data.candle_hub import STEPS


def wick_stop(candles, *, period, side, level, window, now):
    step = STEPS['1Dutc' if period == '1D' else period]
    level = Decimal(str(level))
    if not level.is_finite() or level <= 0 or not window['end'] <= now < window['expires']:
        return None
    for c in sorted(candles, key=lambda r: r['time'], reverse=True):
        t = c['time']
        if not window['end'] <= t < window['expires'] or t+step > now:
            continue
        o,h,l,close = (Decimal(str(c[k])) for k in ('open','high','low','close'))
        if not all(x.is_finite() and x > 0 for x in (o,h,l,close)) or not l <= min(o,close) <= max(o,close) <= h:
            continue
        hit = l < min(o,close) and l <= level <= min(o,close) if side == 'long' else h > max(o,close) and max(o,close) <= level <= h
        if hit:
            return dict(stop_loss=format(l if side=='long' else h,'f'), candle_ms=t, level=format(level,'f'), timeframe=period)
    return None

