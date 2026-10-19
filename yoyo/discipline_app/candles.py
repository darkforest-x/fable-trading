"""Bounded, read-only OKX candle relay, independent of ledgers and execution.

Sources: OKX v5 market/candles and market/history-candles. Cache by contract,
period and older cursor; single-flight fetches never hold a ledger/market lock.
The client supplies no host, credentials, arbitrary endpoint or request body.
"""
from collections import OrderedDict
from concurrent.futures import Future
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request

PERIODS = ('1m', '5m', '15m', '1H', '4H', '1D')


class CandleUnavailable(Exception):
    pass


def fetch_okx(symbol, period, before):
    endpoint = 'history-candles' if before else 'candles'
    params = dict(instId=symbol, bar=period, limit='300')
    if before:
        params['after'] = str(before * 1000)
    url = 'https://www.okx.com/api/v5/market/' + endpoint + '?' + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={'User-Agent': 'SpikeDiscipline/0.8.14'})
    with urllib.request.urlopen(request, timeout=6) as response:
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise CandleUnavailable('K线响应超出范围')
    body = json.loads(payload)
    if body.get('code') != '0' or not isinstance(body.get('data'), list):
        raise CandleUnavailable('OKX K线暂不可用')
    rows = body['data']
    if len(rows) > 300:
        raise CandleUnavailable('K线响应超出范围')
    for row in rows:
        if not isinstance(row, list) or len(row) != 9 or not all(isinstance(v, str) and v for v in row) or row[8] not in ('0', '1'):
            raise CandleUnavailable('K线数据格式异常')
        values = [float(v) for v in row[:8]]
        stamp, opening, high, low, closing, volume, base_volume, quote_volume = values
        if (not all(math.isfinite(v) for v in values) or stamp <= 0 or stamp % 1000
                or min(opening, high, low, closing) <= 0 or high < max(opening, closing)
                or low > min(opening, closing) or min(volume, base_volume, quote_volume) < 0):
            raise CandleUnavailable('K线数据格式异常')
    return rows


class CandleHistory:
    def __init__(self, fetch=fetch_okx, clock=time.monotonic):
        self.fetch, self.clock = fetch, clock
        self.cache = OrderedDict()
        self.pending = {}
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(4)

    def read(self, symbol, period, before=None):
        if (not re.fullmatch(r'[A-Z0-9]{1,40}-USDT-SWAP', symbol) or period not in PERIODS
                or (before is not None and (type(before) is not int or not 0 < before < 10_000_000_000))):
            raise ValueError('不支持的合约、周期或时间')
        key = symbol, period, before
        with self.lock:
            cached = self.cache.get(key)
            if cached and self.clock() < cached[0]:
                self.cache.move_to_end(key)
                if isinstance(cached[1], Exception):
                    raise cached[1]
                return cached[1]
            future = self.pending.get(key)
            leader = future is None
            if leader:
                if not self.slots.acquire(blocking=False):
                    raise CandleUnavailable('K线正在更新，请稍后重试')
                future = self.pending[key] = Future()
        if not leader:
            return future.result(timeout=8)
        try:
            result = dict(code='0', data=self.fetch(symbol, period, before), source='OKX')
            with self.lock:
                self._cache(key, result, 600 if before else 2)
            future.set_result(result)
            return result
        except Exception:
            error = CandleUnavailable('历史K线暂不可用，请重试')
            with self.lock:
                self._cache(key, error, 2)
            future.set_exception(error)
            raise error from None
        finally:
            with self.lock:
                self.pending.pop(key, None)
            self.slots.release()

    def _cache(self, key, value, ttl):
        self.cache[key] = self.clock() + ttl, value
        self.cache.move_to_end(key)
        while len(self.cache) > 64:
            self.cache.popitem(last=False)
