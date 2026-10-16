"""Public OKX candles in memory, with explicit UTC close cutoffs and warmup.

Uses /api/v5/market/history-candles (100 rows/page) and public instruments.
https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks-history
No canonical OHLC cache, scanner checkpoint or forward log is written. Result
snapshots belong only to the independent workbench's reproducible observations.
"""
from __future__ import annotations

import math
from pathlib import Path
import gzip
import json
import sqlite3
from contextlib import closing
from collections import OrderedDict
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
    def __init__(self, client=None, runtime=None, book=None):
        self.client = client or OKX(rate=3)
        self.runtime = Path(runtime) if runtime else None
        self.cache = OrderedDict()
        self.book = book
        self.sources = {}

    def checkpoint(self, symbol, timeframe):
        """Reuse the existing writer's confirmed OHLC, never its MA state."""
        path = self.runtime/'monitor.sqlite3' if self.runtime else None
        if path is None or not path.is_file():return []
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            row=db.execute('SELECT payload FROM candle_checkpoints WHERE symbol=? AND timeframe=?',(symbol,timeframe)).fetchone()
        if not row:return []
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(row[0])) as stream:raw=stream.read(32*1024*1024+1)
        if len(raw)>32*1024*1024:raise ValueError('行情 checkpoint 超过读取上限')
        return json.loads(raw)


    def universe(self):
        self.client.synchronize()
        rows = [r for r in self.client.instruments() if crypto_instrument(r)]
        if not rows:
            raise ValueError('OKX 加密永续合约分类不可用')
        return [dict(symbol=r['instId'], name=r['instId'].split('-')[0],listed_ms=int(r.get('listTime') or 0)) for r in rows]

    def remember(self,key,rows):
        self.cache[key]=rows[-2000:];self.cache.move_to_end(key)
        while len(self.cache)>32:self.cache.popitem(last=False)
        if self.book:self.book.input_cache(*key,rows)

    def candles(self, symbol, timeframe, cutoff, visible_start, cancelled=lambda: False):
        """Read o/h/l/c/v through cutoff; need 1,000 preceding bars for MAs.

        Missing timestamps fail rather than interpolate. Unconfirmed bars and
        bars later than the frozen close cutoff are excluded before enrichment.
        """
        step = TIMEFRAMES[timeframe]
        last_open = cutoff // step * step - step
        first_open = visible_start - WARMUP * step
        after = last_open + 1
        key=(symbol,timeframe)
        cached=self.cache.get(key,[])
        if not cached and self.book:
            cached=self.book.input_cache(*key)
            if cached:self.sources[key]='saved_research_input_checkpoint'
        if not cached and self.runtime:
            cached=self.checkpoint(symbol,timeframe)
            if cached:self.sources[key]='existing_okx_confirmed_checkpoint'
        cached=parse([[r[k] for k in ('t','o','h','l','c','v')]+[0,0,str(r.get('confirm',1))] for r in cached],timeframe,cutoff)
        collected={r['t']:r for r in cached if first_open<=r['t']<=last_open}
        # Only use a contiguous cache that reaches the required warmup origin.
        if not collected or min(collected)!=first_open or any(b-a!=step for a,b in zip(sorted(collected),sorted(collected)[1:])):
            collected={}
        cached_end=max(collected) if collected else None
        if cached_end==last_open:
            self.remember(key,[collected[t] for t in sorted(collected)])
            self.sources.setdefault(key,'prior_okx_observation_in_memory')
            return self.cache[key]
        recent=hasattr(self.client,'clock') and last_open>=self.client.clock()//step*step-1200*step and last_open-first_open<1400*step
        page_size=300 if recent else 100
        path='/api/v5/market/candles' if recent else '/api/v5/market/history-candles'
        self.sources[key]='okx_public_candles_with_cached_prefix' if collected else 'okx_public_candles' if recent else 'okx_public_history_candles'
        # The API request limit is a page size, not a truncation of the job.
        pages = (last_open-first_open)//step//page_size + 4
        for _ in range(pages):
            if cancelled():
                raise Cancelled()
            raw = self.client.get(path,
                                  {'instId':symbol,'bar':timeframe,'limit':str(page_size),'after':str(after)})
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
            if oldest <= (cached_end if cached_end is not None else first_open):
                break
        rows = [collected[t] for t in sorted(collected) if first_open <= t <= last_open]
        if not rows or rows[-1]['t'] != last_open:
            raise ValueError('所选结束时点的已收盘K线尚未到达')
        if rows[0]['t'] > first_open:
            raise ValueError('上市时间或历史覆盖不足：需要额外 1,000 根均线预热')
        if any(b['t']-a['t'] != step for a,b in zip(rows,rows[1:])):
            raise ValueError('所选行情区间存在缺口，未补造K线')
        self.remember(key,rows)
        return rows
