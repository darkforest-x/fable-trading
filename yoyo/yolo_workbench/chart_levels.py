"""Display-only Monday levels anchored to a stored detection's close time.

Reuse the monitor's NY Monday calendar and 1.618 formula. Only the selected
symbol's 24 confirmed Monday hourly OHLC bars are needed; a short detection
snapshot is not a weekly range. Reads share the public candle hub and never
start inference or change notifications, snapshots, or trading eligibility.
"""
from collections import OrderedDict
from copy import deepcopy
from threading import BoundedSemaphore, Lock

from yoyo.monitor.monday_fib import baseline, week_window
from yoyo.monitor.okx import OKX


class ChartLevels:
    def __init__(self, runtime=None, client=None):
        self.client = client or OKX(runtime=runtime, timeout=(3, 5))
        self.cache = OrderedDict()
        self.lock = Lock()
        self.slots = BoundedSemaphore(2)

    def read(self, snapshot, detection):
        box = next((b for b in snapshot.get('episodes', []) + snapshot.get('raw_detections', [])
                    if b['id'] == detection), None)
        if box is None:
            raise KeyError(detection)
        at = box['detected_at_ms']
        window = week_window(at)
        result = dict(symbol=snapshot['symbol'], detection_id=detection, as_of_ms=at,
                      timezone='America/New_York', week=window['week'],
                      start_ms=window['start'], end_ms=window['end'], expires_ms=window['expires'])
        # Never borrow the previous week or the completed future Monday.
        if at < window['end']:
            return dict(result, state='developing')
        key = (snapshot['symbol'], window['start'])
        with self.lock:
            cached = self.cache.get(key)
            if cached:
                self.cache.move_to_end(key)
                return dict(result, **deepcopy(cached))
        if not self.slots.acquire(False):
            raise ValueError('区间正在读取，请稍后重试')
        try:
            raw = self.client.get('/api/v5/market/history-candles',
                                  dict(instId=snapshot['symbol'], bar='1H', limit='24',
                                       after=str(window['end'])))
            value = baseline(raw, at)
            if not all(k in value for k in ('high', 'low', 'upper', 'lower')):
                return dict(result, state=value['state'])
            # baseline's later-hour coverage gate governs alert arming, not
            # whether an already complete Monday can be drawn in a chart.
            levels = dict(state='ready', **{k: value[k] for k in ('high', 'low', 'upper', 'lower')})
            with self.lock:
                self.cache[key] = levels
                self.cache.move_to_end(key)
                while len(self.cache) > 128:
                    self.cache.popitem(last=False)
            return dict(result, **levels)
        finally:
            self.slots.release()
