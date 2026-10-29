"""Demand-only historical chart pages and NY weekly display ranges.

OKX native history uses exclusive `after` timestamps and the shared CandleHub.
Only BTC/ETH/XAU may page beyond the 720-bar live snapshot. Completed Monday
anchors retain the existing confirmed 5m reader, extrema and NY/DST calendar.
No scanner subscriptions, execution context or signal/notification writes.
Sources: https://www.okx.com/docs-v5/en/#order-book-trading-market-data
"""
from threading import BoundedSemaphore, Lock

from fastapi import Query

from yoyo.data.candle_hub import STEPS, validate
from .monday_fib import week_window
from .chart_periods import PERIODS, native_bar, step_ms, open_time, candles as display_candles

FULL_HISTORY = frozenset(('BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'XAU-USDT-SWAP'))


class ChartHistory:
    def __init__(self, candles, levels):
        self.candles, self.levels = candles, levels
        self.slots = BoundedSemaphore(1)
        self.listings, self.lock = {}, Lock()

    def listing(self, symbol):
        with self.lock:
            if symbol in self.listings:
                return self.listings[symbol]
        rows = self.candles.client.get('/api/v5/public/instruments',
                                       dict(instType='SWAP', instId=symbol))
        row = next((r for r in rows if r.get('instId') == symbol), {})
        stamp = int(row.get('listTime') or 0)
        if stamp <= 0:
            raise ValueError('listing_unavailable')
        with self.lock:
            self.listings[symbol] = stamp
        return stamp

    def read(self, symbol, period, before_ms):
        client = self.candles.client
        bar = native_bar(period)
        now, step = client.clock(), step_ms(period)
        before_ms = min(open_time(before_ms, period), open_time(now, period))
        value = dict(symbol=symbol, bar=period, native_bar=bar, source='OKX',
                     before_ms=before_ms, candles=[], next_before_ms=before_ms,
                     has_more=True, state='ready', error=None)
        if symbol not in FULL_HISTORY:
            return dict(value, state='limited', has_more=False, limit=720)
        if not self.slots.acquire(False):
            return dict(value, state='retry', error='历史 K 线正在读取，请稍后重试')
        try:
            first = open_time(self.listing(symbol), period)
            value['listed_ms'] = first
            if before_ms <= first:
                return dict(value, state='exhausted', has_more=False)
            raw = client.get('/api/v5/market/history-candles',
                             dict(instId=symbol, bar=bar, limit='300', after=str(before_ms)))
            rows = {t:r for t,r in validate(raw,bar,now).items()
                    if r[8]=='1' and first<=t<before_ms and t+STEPS[bar]<=now}
            if not rows:
                # Empty/short exchange responses do not prove the listing end.
                return dict(value, state='retry', error='这段历史暂不可用，可重试')
            bars = display_candles(rows, period)
            if not bars:
                return dict(value, state='exhausted' if min(rows) < first+step else 'retry',
                            has_more=min(rows) >= first+step, error=None if min(rows) < first+step else '历史 K 线不完整，可重试')
            cursor = bars[0]['time']
            return dict(value, candles=bars, next_before_ms=cursor,
                        has_more=cursor>first, state='ready' if cursor>first else 'exhausted')
        except Exception:
            return dict(value, state='retry', error='历史 K 线读取失败，可重试')
        finally:
            self.slots.release()

    def weeks(self, symbol, from_ms, to_ms):
        """At most three weeks per request; cursor continues without a total cap."""
        now = self.candles.client.clock()
        cursor, items = min(to_ms, now+1), []
        if from_ms >= cursor:
            return dict(symbol=symbol, timezone='America/New_York', weeks=[], next_before_ms=None)
        for _ in range(3):
            window = week_window(cursor-1)
            items.append(self.levels.read(symbol, min(now, window['end'])))
            cursor = window['start']
            if cursor <= from_ms:
                break
        return dict(symbol=symbol, timezone='America/New_York', weeks=items,
                    next_before_ms=cursor if cursor>from_ms else None)


def install(app, candles, levels):
    service = ChartHistory(candles, levels)
    app.state.monday_chart_history = service
    symbol_pattern = r'^[A-Z0-9]{1,32}-USDT-SWAP$'

    @app.get('/api/monday-fib/history')
    def history(symbol: str = Query(..., pattern=symbol_pattern),
                period: str = Query('15m', pattern='^('+'|'.join(PERIODS)+')$'),
                before_ms: int = Query(..., gt=0)):
        return service.read(symbol, period, before_ms)

    @app.get('/api/monday-fib/weekly-levels')
    def weekly_levels(symbol: str = Query(..., pattern=symbol_pattern),
                      from_ms: int = Query(..., gt=0), to_ms: int = Query(..., gt=0)):
        try:
            return service.weeks(symbol, from_ms, to_ms)
        except Exception:
            return dict(symbol=symbol, timezone='America/New_York', weeks=[],
                        next_before_ms=to_ms, error='历史周一区间暂不可用，可重试')
