"""Demand-only historical chart pages and NY weekly display ranges.

OKX native history uses exclusive `after` timestamps and the shared CandleHub.
Any selected SWAP may page one native history response at a time. A successful
empty history response marks the exchange API's available-data boundary;
request failures remain retryable. Completed Monday anchors retain the
existing confirmed 5m reader, extrema and NY/DST calendar.
No scanner subscriptions, execution context or signal/notification writes.
Sources: https://www.okx.com/docs-v5/en/#order-book-trading-market-data
"""
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError
from copy import deepcopy
from threading import BoundedSemaphore, Lock
from time import monotonic

from fastapi import HTTPException, Query
from fastapi.responses import JSONResponse

from yoyo.data.candle_hub import STEPS, validate
from .monday_fib import week_window
from .chart_periods import PERIODS, native_bar, step_ms, open_time, candles as display_candles

HISTORY_PAGE_SIZE = 300
# Owner 2026-11-06 (replay "k线加载不出来"): one global slot made a second chart's
# page fail at once and the browser then stopped paging until a manual retry.
# Allow a few concurrent reads and queue the rest briefly; the shared CandleHub
# still enforces the OKX request budget.
HISTORY_SLOTS = 3
SLOT_WAIT_SECONDS = 10
# Replaying views can request the same closed page together; only successful
# exact-key pages are reused for this brief overlap window.
HISTORY_PAGE_CACHE_SECONDS = 2
HISTORY_PAGE_CACHE_SIZE = 12


class ChartHistory:
    def __init__(self, candles, levels):
        self.candles, self.levels = candles, levels
        self.slots = BoundedSemaphore(HISTORY_SLOTS)
        self.slot_wait = SLOT_WAIT_SECONDS
        self.lock = Lock()
        self.windows = OrderedDict()
        self.pages = OrderedDict()
        self.page_inflight = {}

    def trade_window(self, symbol, period, entry_ms, exit_ms):
        """Read a bounded public OHLC window around a saved paper fill.

        Match the backtest chart's 240 pre-entry / 80 post-exit bars, using
        OKX candles and the entry week's rays. This bypasses only the general
        scroll whitelist, never the live snapshot, scanner or execution cache.
        """
        client = self.candles.client
        now, step, bar = client.clock(), step_ms(period), native_bar(period)
        if not 0 < entry_ms <= exit_ms <= now:
            raise ValueError('交易时间无效')
        if (exit_ms - entry_ms) // step > 5000:
            raise ValueError('交易跨度过长，请切换更大周期')
        start = max(0, open_time(entry_ms, period) - 240 * step)
        end = min(open_time(exit_ms, period) + 81 * step, open_time(now, period))
        key = (symbol, period, entry_ms, exit_ms, end)
        with self.lock:
            if key in self.windows:
                self.windows.move_to_end(key)
                return deepcopy(self.windows[key])
        if not self.slots.acquire(timeout=self.slot_wait):
            raise RuntimeError('历史 K 线正在读取，请稍后重试')
        try:
            rows, cursor = {}, end
            # Native 1m pairs for 2m; bounded even if upstream pages are short.
            pages = (end - start + 300 * STEPS[bar] - 1) // (300 * STEPS[bar]) + 2
            for _ in range(pages):
                raw = client.get('/api/v5/market/history-candles',
                                 dict(instId=symbol, bar=bar, limit='300', after=str(cursor)))
                page = {t: r for t, r in validate(raw, bar, now).items()
                        if r[8] == '1' and t < cursor and t + STEPS[bar] <= end}
                if not page:
                    break
                rows.update(page)
                cursor = min(page)
                if cursor <= start:
                    break
            candles = [b for b in display_candles(rows, period) if start <= b['time'] < end]
            times = {b['time'] for b in candles}
            if open_time(entry_ms, period) not in times or open_time(exit_ms, period) not in times:
                raise RuntimeError('开平仓时段的已收盘 K 线暂不可用，请稍后重试或切换周期')
            if any(b['time'] - a['time'] != step for a, b in zip(candles, candles[1:])):
                raise RuntimeError('历史 K 线存在缺口，请重新加载')
            anchors = self.levels.read_display(symbol, entry_ms)
            if anchors.get('state') != 'ready':
                raise RuntimeError('开仓时的周一区间暂不可用，请重新加载')
            value = dict(schema=1, symbol=symbol, source='OKX', bar=period, native_bar=bar,
                         timezone='America/New_York', entry_ms=entry_ms, exit_ms=exit_ms,
                         candles=candles, weekly_ranges=[anchors], paperHistory=True,
                         now_ms=end, last_close_ms=end)
            with self.lock:
                self.windows[key] = value
                self.windows.move_to_end(key)
                while len(self.windows) > 12:
                    self.windows.popitem(last=False)
            return deepcopy(value)
        finally:
            self.slots.release()

    def _read_page(self, client, symbol, period, before_ms, bar, now, value):
        """Fetch one page while holding a history slot; failures stay retryable."""
        if not self.slots.acquire(timeout=self.slot_wait):
            return dict(value, state='retry', error='历史 K 线正在读取，请稍后重试')
        try:
            raw = client.get('/api/v5/market/history-candles',
                             dict(instId=symbol, bar=bar, limit=str(HISTORY_PAGE_SIZE), after=str(before_ms)))
            if isinstance(raw, list) and not raw:
                return dict(value, state='exhausted', has_more=False,
                            end_reason='exchange_api_boundary')
            rows = {t:r for t,r in validate(raw,bar,now).items()
                    if r[8]=='1' and t<before_ms and t+STEPS[bar]<=now}
            if not rows:
                return dict(value, state='retry', error='这段历史暂不可用，可重试')
            bars = display_candles(rows, period)
            if not bars:
                return dict(value, state='retry', error='历史 K 线不完整，可重试')
            return dict(value, candles=bars, next_before_ms=bars[0]['time'],
                        has_more=True, state='ready')
        except Exception:
            return dict(value, state='retry', error='历史 K 线读取失败，可重试')
        finally:
            self.slots.release()

    def read(self, symbol, period, before_ms):
        client = self.candles.client
        bar = native_bar(period)
        now = client.clock()
        before_ms = min(open_time(before_ms, period), open_time(now, period))
        value = dict(symbol=symbol, bar=period, native_bar=bar, source='OKX',
                     before_ms=before_ms, candles=[], next_before_ms=before_ms,
                     has_more=True, state='ready', error=None)
        key = (symbol, period, before_ms)
        cached = None
        leader = False
        with self.lock:
            page = self.pages.get(key)
            if page is not None:
                expires, cached = page
                if expires <= monotonic():
                    del self.pages[key]
                    cached = None
                else:
                    self.pages.move_to_end(key)
            if cached is None:
                future = self.page_inflight.get(key)
                if future is None:
                    future = Future()
                    self.page_inflight[key] = future
                    leader = True
        if cached is not None:
            return deepcopy(cached)
        if not leader:
            try:
                return deepcopy(future.result(timeout=self.slot_wait))
            except TimeoutError:
                return dict(value, state='retry', error='历史 K 线正在读取，请稍后重试')

        result = None
        shared_result = None
        try:
            result = self._read_page(client, symbol, period, before_ms, bar, now, value)
            shared_result = deepcopy(result)
            if result['state'] == 'ready':
                with self.lock:
                    self.pages[key] = (monotonic()+HISTORY_PAGE_CACHE_SECONDS, shared_result)
                    self.pages.move_to_end(key)
                    while len(self.pages) > HISTORY_PAGE_CACHE_SIZE:
                        self.pages.popitem(last=False)
            return result
        finally:
            with self.lock:
                active = self.page_inflight.pop(key, None)
                if active is future and not future.done():
                    if shared_result is None:
                        shared_result = deepcopy(dict(value, state='retry', error='历史 K 线读取失败，可重试'))
                    future.set_result(shared_result)

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

    @app.get('/api/monday-fib/trade-candles')
    def trade_candles(symbol: str = Query(..., pattern=symbol_pattern),
                      period: str = Query('15m', pattern='^('+'|'.join(PERIODS)+')$'),
                      entry_ms: int = Query(..., gt=0), exit_ms: int = Query(..., gt=0)):
        try:
            return service.trade_window(symbol, period, entry_ms, exit_ms)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        except RuntimeError as error:
            raise HTTPException(503, str(error)) from None
        except Exception:
            raise HTTPException(503, '历史交易 K 线读取失败，请重新加载') from None

    @app.get('/api/monday-fib/history')
    def history(symbol: str = Query(..., pattern=symbol_pattern),
                period: str = Query('15m', pattern='^('+'|'.join(PERIODS)+')$'),
                before_ms: int = Query(..., gt=0)):
        return JSONResponse(service.read(symbol, period, before_ms))

    @app.get('/api/monday-fib/weekly-levels')
    def weekly_levels(symbol: str = Query(..., pattern=symbol_pattern),
                      from_ms: int = Query(..., gt=0), to_ms: int = Query(..., gt=0)):
        try:
            return service.weeks(symbol, from_ms, to_ms)
        except Exception:
            return dict(symbol=symbol, timezone='America/New_York', weeks=[],
                        next_before_ms=to_ms, error='历史周一区间暂不可用，可重试')
