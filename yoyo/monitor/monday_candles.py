"""Demand-only native candles for the Monday wallpaper, separate from scanning.

OKX bar/confirm contract: https://app.okx.com/docs-v5/en/#order-book-trading-market-data
Daily candles use 1Dutc; NY Monday anchors still come from MondayFibService.
Only the selected symbol/period is read through the shared public candle hub.
Two history jobs, two live-tail readers and twelve cached history views cannot
expand the monitor universe. Closed snapshots remain separate from live tails.
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
    def __init__(self, runtime=None, client=None, live_client=None):
        self.client = client or OKX(runtime=runtime)
        # Display-only tail. Never change the closed client's cache contract:
        # order-context consumes snapshot(), not this live endpoint.
        self.live_client = live_client or OKX(runtime=runtime, include_open=True)
        self.live_slots = threading.BoundedSemaphore(2)
        self.lock = threading.Lock()
        self.ready = threading.Condition(self.lock)
        # Waiting clients cannot exhaust the API's sync worker pool.
        self.waiters = threading.BoundedSemaphore(8)
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
            # Reuse the closed history only once a new page overlaps it. Long
            # absences still backfill normally instead of joining across a gap.
            previous = {r['time']: r for r in entry['candles']}
            complete = entry.get('history_complete', False)
            overlap = False
            for page_index in range(3):
                raw = self.client.get('/api/v5/market/history-candles',
                                      dict(instId=symbol, bar=bar, limit='240', after=str(cursor)))
                page = validate(raw, bar, now)
                page = {t: r for t, r in page.items() if r[8] == '1' and t < cursor and t + STEPS[bar] <= boundary}
                if not page:
                    break
                rows.update(page)
                if complete and previous and any(t in previous for t in page):
                    overlap = True
                    break
                if page_index == 0:
                    # A first screen needs 160 bars, not all three history pages.
                    # Publish only validated closed bars; history keeps the same
                    # bounded job and shared upstream rate budget.
                    first = self._candles(rows)
                    with self.ready:
                        entry.update(candles=first, last_close_ms=first[-1]['time']+STEPS[bar],
                                     generated_ms=now, history_complete=False, error=None)
                        self.ready.notify_all()
                cursor = min(page)
                if len(page) < 240:
                    break
            candles = self._candles(rows)
            if overlap:
                previous.update({r['time']: r for r in candles})
                candles = [previous[t] for t in sorted(previous)][-720:]
            # An upstream page can lag a just-closed bar. Retry that boundary;
            # never call an older tail fresh merely because a fetch succeeded.
            last_close = candles[-1]['time'] + STEPS[bar] if candles else 0
            with self.lock:
                entry.update(candles=candles, last_close_ms=last_close, generated_ms=now,
                             history_complete=True, error=None)
        except Exception:
            with self.lock:
                entry['error'] = 'OKX K 线暂不可用，请重试'
        finally:
            with self.ready:
                entry.update(loading=False, retry_ms=self.client.clock()+10000)
                self.ready.notify_all()
            self.slots.release()

    @staticmethod
    def _candles(rows):
        return [dict(time=t, open=float(r[1]), high=float(r[2]), low=float(r[3]),
                     close=float(r[4]), volume=float(r[5])) for t, r in sorted(rows.items())]

    def recent(self, symbol, period):
        """Native display OHLC, with the forming candle explicitly separated.

        Uses the shared two-second transient cache and background rate budget.
        No subscriptions, scanner work, or writes to the closed snapshot.
        """
        if not re.fullmatch(SYMBOL, symbol) or period not in PERIODS:
            raise ValueError('unsupported_chart_query')
        bar = '1Dutc' if period == '1D' else period
        value = dict(symbol=symbol, bar=period, native_bar=bar,
                     closed_candles=[], forming_candle=None, error=None)
        if self.closed or not self.live_slots.acquire(False):
            return dict(value, now_ms=self.client.clock(), error='实时 K 线正在重连')
        try:
            raw = self.live_client.get('/api/v5/market/candles',
                                       dict(instId=symbol, bar=bar, limit='3'))
            now = self.live_client.clock()
            boundary = bar_open(now, bar)
            rows = validate(raw, bar, now)
            closed = {t:r for t,r in rows.items() if r[8]=='1' and t+STEPS[bar]<=boundary}
            forming = {t:r for t,r in rows.items() if r[8]=='0' and t==boundary}
            value.update(closed_candles=self._candles(closed),
                         forming_candle=next(iter(self._candles(forming)), None))
            if not closed and not forming:
                value['error'] = '实时 K 线暂不可用'
        except Exception:
            value['error'] = '实时 K 线暂不可用'
        finally:
            self.live_slots.release()
        return dict(value, now_ms=self.live_client.clock())

    def snapshot(self, symbol, period, wait_ms=0):
        if not re.fullmatch(SYMBOL, symbol) or period not in PERIODS:
            raise ValueError('unsupported_chart_query')
        now = self.client.clock()
        bar = '1Dutc' if period == '1D' else period
        with self.ready:
            key = (symbol, period)
            entry = self.cache.setdefault(key, dict(candles=[], loading=False, last_close_ms=0))
            self.cache.move_to_end(key)
            due = (entry['last_close_ms'] < bar_open(now, bar) or not entry['candles']
                   or not entry.get('history_complete') or bool(entry.get('error')))
            if not self.closed and due and not entry['loading'] and entry.get('retry_ms', 0) <= now:
                if self.slots.acquire(False):
                    entry['loading'] = True
                    self.pool.submit(self._fetch, symbol, period, entry)
                else:
                    entry['error'] = 'K 线正在更新，请稍后重试'
            if wait_ms > 0 and not entry['candles'] and entry['loading'] and self.waiters.acquire(False):
                try:
                    self.ready.wait_for(lambda: bool(entry['candles']) or not entry['loading'] or self.closed,
                                        timeout=min(wait_ms, 8000)/1000)
                finally:
                    self.waiters.release()
            value = deepcopy(entry)
            while len(self.cache) > 12:
                removable = next((k for k, v in self.cache.items() if not v['loading']), None)
                if removable is None:
                    break
                del self.cache[removable]
        value.pop('retry_ms', None)
        now = self.client.clock()
        window = week_window(now)
        return dict(value, now_ms=now, next_close_ms=bar_open(now, bar)+STEPS[bar],
                    symbol=symbol, bar=period, native_bar=bar, source='OKX',
                    timezone='America/New_York', week=window['week'],
                    monday=dict(start_ms=window['start'], end_ms=window['end'], expires_ms=window['expires']))

    def close(self):
        with self.ready:
            self.closed = True
            self.ready.notify_all()
        self.pool.shutdown(wait=False, cancel_futures=True)


def install(app, runtime):
    service = MondayCandles(runtime)
    app.state.monday_candles = service
    from .monday_order_context import install as install_order_context
    install_order_context(app)

    @app.get('/api/monday-fib/candles')
    def candles(symbol: str = Query(..., pattern=SYMBOL),
                period: str = Query('15m', pattern=r'^(1m|5m|15m|30m|1H|4H|1D)$'),
                wait_ms: int = Query(0, ge=0, le=8000)):
        return service.snapshot(symbol, period, wait_ms)

    @app.get('/api/monday-fib/live-candles')
    def live_candles(symbol: str = Query(..., pattern=SYMBOL),
                     period: str = Query('15m', pattern=r'^(1m|5m|15m|30m|1H|4H|1D)$')):
        return service.recent(symbol, period)
