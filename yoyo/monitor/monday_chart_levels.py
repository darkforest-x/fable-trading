"""Display-only Monday extrema and ray anchors from confirmed 5m OHLC.

Use the existing NY Monday calendar and mirrored 1.618 formula. One bounded
read of the selected Monday supplies 288 bars, including the first bar that
sets each extreme. Cache completed weeks only. No signal or alert writes.
"""
from collections import OrderedDict
from threading import BoundedSemaphore, Lock

from yoyo.data.candle_hub import validate
from yoyo.monitor.monday_fib import week_window
from yoyo.monitor.okx import OKX


class MondayChartLevels:
    def __init__(self, runtime=None, client=None):
        self.client = client or OKX(runtime=runtime, timeout=(3, 5))
        self.cache = OrderedDict()
        self.lock = Lock()
        self.slots = BoundedSemaphore(2)

    def read(self, symbol, at):
        window = week_window(at)
        result = dict(symbol=symbol, timezone='America/New_York', week=window['week'],
                      start_ms=window['start'], end_ms=window['end'], expires_ms=window['expires'],
                      anchor_bar='5m')
        if at < window['end']:
            return dict(result, state='developing')
        key = (symbol, window['start'])
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return dict(result, **self.cache[key])
        if not self.slots.acquire(False):
            raise ValueError('区间正在读取，请稍后重试')
        try:
            raw = self.client.get('/api/v5/market/history-candles',
                                  dict(instId=symbol, bar='5m', limit='300', after=str(window['end'])))
            rows = validate(raw, '5m', at)
            monday = list(range(window['start'], window['end'], 300_000))
            if any(t not in rows or rows[t][8] != '1' for t in monday):
                return dict(result, state='missing_monday')
            high_ms = max(monday, key=lambda t: float(rows[t][2]))
            low_ms = min(monday, key=lambda t: float(rows[t][3]))
            high, low = float(rows[high_ms][2]), float(rows[low_ms][3])
            if high <= low:
                return dict(result, state='invalid_range')
            value = dict(state='ready', high=high, low=low, high_ms=high_ms, low_ms=low_ms,
                         upper=high+.618*(high-low), lower=low-.618*(high-low))
            with self.lock:
                self.cache[key] = value
                self.cache.move_to_end(key)
                while len(self.cache) > 128:
                    self.cache.popitem(last=False)
            return dict(result, **value)
        finally:
            self.slots.release()
