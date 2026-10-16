"""Public OKX candles in memory, with explicit UTC close cutoffs and warmup.

Uses /api/v5/market/history-candles (100 rows/page) and public instruments.
https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks-history
No canonical OHLC cache, scanner checkpoint or forward log is written. Result
snapshots belong only to the independent workbench's reproducible observations.
"""
from __future__ import annotations

import math
from yoyo.monitor.okx import OKX
from yoyo.monitor.altcoin_leaders import crypto_instrument
from .models import TIMEFRAMES

WARMUP = 1000


class Cancelled(Exception):
    """Cooperative cancellation between network pages and inference batches."""


def parse(rows, timeframe, cutoff):
    step = TIMEFRAMES[timeframe]
    values = {}
    for raw in rows:
        if not isinstance(raw, list) or len(raw) < 9:
            raise ValueError('OKX K线结构异常')
        if str(raw[8]) == '0':
            continue
        if str(raw[8]) != '1':
            raise ValueError('OKX 收盘确认字段异常')
        stamp = int(raw[0])
        if stamp + step > cutoff:
            continue
        o,h,low,c,v = map(float, raw[1:6])
        if stamp % step or not all(math.isfinite(x) for x in (o,h,low,c,v)) or min(o,h,low,c) <= 0 or v < 0 or h < max(o,low,c) or low > min(o,h,c):
            raise ValueError('K线时间或价格不合法')
        row = dict(t=stamp,o=o,h=h,l=low,c=c,v=v)
        if stamp in values and values[stamp] != row:
            raise ValueError('同一收盘K线有冲突值')
        values[stamp] = row
    return list(values.values())


class Market:
    def __init__(self, client=None):
        self.client = client or OKX(rate=3)

    def universe(self):
        self.client.synchronize()
        rows = [r for r in self.client.instruments() if crypto_instrument(r)]
        if not rows:
            raise ValueError('OKX 加密永续合约分类不可用')
        return [dict(symbol=r['instId'], name=r['instId'].split('-')[0]) for r in rows]

    def candles(self, symbol, timeframe, cutoff, visible_start, cancelled=lambda: False):
        """Read o/h/l/c/v through cutoff; need 1,000 preceding bars for MAs.

        Missing timestamps fail rather than interpolate. Unconfirmed bars and
        bars later than the frozen close cutoff are excluded before enrichment.
        """
        step = TIMEFRAMES[timeframe]
        last_open = cutoff // step * step - step
        first_open = visible_start - WARMUP * step
        after = last_open + 1
        collected = {}
        # The API request limit is a page size, not a truncation of the job.
        pages = (last_open-first_open)//step//100 + 4
        for _ in range(pages):
            if cancelled():
                raise Cancelled()
            raw = self.client.get('/api/v5/market/history-candles',
                                  {'instId':symbol,'bar':timeframe,'limit':'100','after':str(after)})
            if not raw:
                break
            batch = parse(raw,timeframe,cutoff)
            for r in batch:
                if r['t'] in collected and collected[r['t']] != r:
                    raise ValueError('翻页期间已收盘K线被修订')
                collected[r['t']] = r
            oldest = min(int(r[0]) for r in raw)
            if oldest >= after:
                raise ValueError('OKX 分页未向前推进')
            after = oldest
            if oldest <= first_open:
                break
        rows = [collected[t] for t in sorted(collected) if first_open <= t <= last_open]
        if not rows or rows[-1]['t'] != last_open:
            raise ValueError('所选结束时点的已收盘K线尚未到达')
        if rows[0]['t'] > first_open:
            raise ValueError('上市时间或历史覆盖不足：需要额外 1,000 根均线预热')
        if any(b['t']-a['t'] != step for a,b in zip(rows,rows[1:])):
            raise ValueError('所选行情区间存在缺口，未补造K线')
        return rows
