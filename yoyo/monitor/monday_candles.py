"""Demand-only native candles for the Monday wallpaper, separate from scanning.

OKX bar/confirm contract: https://app.okx.com/docs-v5/en/#order-book-trading-market-data
Daily candles use 1Dutc; NY Monday anchors still come from MondayFibService.
Only the selected symbol/period is read through the shared public candle hub.
Two bounded jobs and twelve cached views cannot expand the monitor universe.
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import re
import threading

from fastapi import Query
from yoyo.data.candle_hub import STEPS, bar_open, validate
from yoyo.monitor.monday_fib import week_window
from yoyo.monitor.okx import OKX

PERIODS = ('1m', '5m', '15m', '30m', '1H', '4H', '1D')
SYMBOL = r'^[A-Z0-9]{1,32}-USDT-SWAP$'


class MondayCandles:
    def __init__(self, runtime=None, client=None):
        self.client = client or OKX(runtime=runtime)
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='monday-chart')
        self.slots = threading.BoundedSemaphore(2)
        self.closed = False

    def _fetch(self, symbol, period, entry):
        bar = '1Dutc' if period == '1D' else period
        now = self.client.clock()
        boundary = bar_open(now, bar)
        try:
            rows = {}
            cursor = boundary
            for _ in range(3):
                raw = self.client.get('/api/v5/market/history-candles',
                                      dict(instId=symbol, bar=bar, limit='240', after=str(cursor)))
                page = validate(raw, bar, now)
                page = {t: r for t, r in page.items() if r[8] == '1' and t < cursor and t + STEPS[bar] <= boundary}
                if not page:
                    break
                rows.update(page)
                cursor = min(page)
                if len(page) < 240:
                    break
            candles = [dict(time=t, open=float(r[1]), high=float(r[2]), low=float(r[3]),
                            close=float(r[4]), volume=float(r[5])) for t, r in sorted(rows.items())]
            with self.lock:
                entry.update(candles=candles, last_close_ms=boundary, generated_ms=now, error=None)
        except Exception:
            with self.lock:
                entry['error'] = 'OKX K 线暂不可用，请重试'
        finally:
            with self.lock:
                entry.update(loading=False, retry_ms=self.client.clock()+10000)
            self.slots.release()

    def snapshot(self, symbol, period):
        if not re.fullmatch(SYMBOL, symbol) or period not in PERIODS:
            raise ValueError('unsupported_chart_query')
        now = self.client.clock()
        window = week_window(now)
        bar = '1Dutc' if period == '1D' else period
        with self.lock:
            key = (symbol, period)
            entry = self.cache.setdefault(key, dict(candles=[], loading=False, last_close_ms=0))
            self.cache.move_to_end(key)
            due = entry['last_close_ms'] < bar_open(now, bar) or not entry['candles']
            if not self.closed and due and not entry['loading'] and entry.get('retry_ms', 0) <= now:
                if self.slots.acquire(False):
                    entry['loading'] = True
                    self.pool.submit(self._fetch, symbol, period, entry)
                else:
                    entry['error'] = 'K 线正在更新，请稍后重试'
            value = deepcopy(entry)
            while len(self.cache) > 12:
                removable = next((k for k, v in self.cache.items() if not v['loading']), None)
                if removable is None:
                    break
                del self.cache[removable]
        value.pop('retry_ms', None)
        return dict(value, symbol=symbol, bar=period, native_bar=bar, source='OKX',
                    timezone='America/New_York', week=window['week'],
                    monday=dict(start_ms=window['start'], end_ms=window['end']))

    def close(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)


def install(app, runtime):
    service = MondayCandles(runtime)
    app.state.monday_candles = service

    @app.get('/api/monday-fib/candles')
    def candles(symbol: str = Query(..., pattern=SYMBOL),
                period: str = Query('15m', pattern=r'^(1m|5m|15m|30m|1H|4H|1D)$')):
        return service.snapshot(symbol, period)
