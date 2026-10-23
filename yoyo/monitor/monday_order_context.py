"""Demand-only wick stops, using the selected native chart timeframe.

Only closed OHLC from this NY week after Monday's range has completed is read.
The most recent wick must contain the level; a body crossing is not a wick.
Stop distance never determines quantity. Two bounded readers cannot add work
to the forward scanner. Missing data stays unavailable instead of fabricating
a stop or reusing last week's touch.
"""
import threading
from decimal import Decimal
from fastapi import Query
from yoyo.data.candle_hub import STEPS, bar_open, validate
from .monday_fib import week_window
from .monday_candles import SYMBOL


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


def install(app):
    slots = threading.BoundedSemaphore(2)
    cache, lock = {}, threading.Lock()

    @app.get('/api/monday-fib/order-context')
    def context(symbol: str = Query(..., pattern=SYMBOL),
                period: str = Query('15m', pattern=r'^(1m|5m|15m|30m|1H|4H|1D)$'),
                side: str = Query('long', pattern=r'^(long|short)$')):
        service = app.state.monday_candles
        now = service.client.clock()
        window = week_window(now)
        bar = '1Dutc' if period=='1D' else period
        boundary = bar_open(now, bar)
        state = app.state.monday_fib.snapshot(compact=True)
        row = next((r for r in state['items'] if r['symbol']==symbol), {})
        level = row.get('lower' if side=='long' else 'upper')
        result = dict(symbol=symbol, timeframe=period, side=side, week=window['week'], stop_loss=None)
        if state['week'] != window['week'] or row.get('state') != 'ready' or not level or now < window['end']:
            return dict(result, reason='本周周一区间尚未就绪，请手动填写止损')
        key = (symbol,period,side,boundary,level)
        with lock:
            if key in cache:
                return cache[key]
        if not slots.acquire(blocking=False):
            return dict(result, reason='插针 K 线正在读取，请点自动止损重试')
        try:
            snapshot = service.snapshot(symbol, period, wait_ms=8000)
            rows = snapshot['candles']
            # A fresh chart usually already contains the newest eligible wick.
            found = wick_stop(rows, period=period,side=side,level=level,window=window,now=boundary) if snapshot.get('last_close_ms')==boundary else None
            cursor = min((r['time'] for r in rows), default=boundary) if snapshot.get('last_close_ms')==boundary else boundary
            while not found and cursor > window['end']:
                raw = service.client.get('/api/v5/market/history-candles', dict(instId=symbol,bar=bar,limit='240',after=str(cursor)))
                page = {t:r for t,r in validate(raw,bar,now).items() if r[8]=='1' and t<cursor and t+STEPS[bar]<=boundary}
                if cursor==boundary and (not page or max(page)+STEPS[bar]!=boundary):
                    return dict(result,reason='最新已收盘 K 线尚未到齐，请稍后重试')
                if not page:
                    break
                found = wick_stop(service._candles(page),period=period,side=side,level=level,window=window,now=boundary)
                cursor = min(page)
            result.update(found or dict(reason='本周该周期没有影线触及 1.618 的已收盘 K 线，请手动填写止损'))
            with lock:
                if len(cache)>120:
                    cache.clear()
                cache[key] = result
            return result
        except Exception:
            return dict(result, reason='当前周期 K 线暂不可用，请重试或手动填写止损')
        finally:
            slots.release()
