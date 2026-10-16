"""Append display-only confirmed candles to an immutable detection snapshot.

SMA uses trailing source prices (20/60/120); EMA continues the stored prefix
state with alpha=2/(period+1). Only o/h/l/c/v after the snapshot are read. No
past candle, box, model input or observation book is rewritten by this view.
"""
from collections import OrderedDict, deque
import math
from threading import Lock
import time

from .market import Market, parse
from .models import TIMEFRAMES

MAX_TAIL = 3000


def extend(snapshot, rows):
    """Continue close/HL2 trailing SMA and prefix EMA without reseeding."""
    source = snapshot['ma_source']
    if source not in ('close', 'hl2'):
        raise ValueError('未知均线来源，无法续接图表')
    def price(r):
        return r['close'] if source == 'close' else (r['high']+r['low'])/2
    old = snapshot['candles']
    if len(old) < 120:
        raise ValueError('原图不足120根，无法连续计算均线')
    prices = deque((price(r) for r in old[-120:]), maxlen=120)
    emas = {p: old[-1][f'ema{p}'] for p in (20,60,120)}
    if not all(v is not None and math.isfinite(v) for v in emas.values()):
        raise ValueError('原图均线状态不可用')
    output = []
    for raw in rows:
        row = dict(t=raw['t'],open=raw['o'],high=raw['h'],low=raw['l'],close=raw['c'],volume=raw['v'])
        value = price(row)
        prices.append(value)
        for period in (20,60,120):
            row[f'sma{period}'] = sum(list(prices)[-period:])/period
            alpha = 2/(period+1)
            emas[period] = value*alpha+emas[period]*(1-alpha)
            row[f'ema{period}'] = emas[period]
        output.append(row)
    return output


class ChartTail:
    def __init__(self, runtime=None, market=None):
        self.market = market or Market(runtime=runtime)
        self.lock = Lock()
        self.cache = OrderedDict()
        self.synced_at = -math.inf

    def read(self, identity, snapshot):
        with self.lock:
            if time.monotonic()-self.synced_at > 60:
                self.market.client.synchronize()
                self.synced_at = time.monotonic()
            clock = self.market.client.clock()
            step = TIMEFRAMES[snapshot['timeframe']]
            cutoff = clock//step*step
            start = snapshot['candles'][-1]['t']+step
            if cutoff-start > MAX_TAIL*step:
                raise ValueError('这条记录距今超过3,000根K线，请选择较近的检测记录；原始图表仍可查看')
            key = (identity, snapshot['symbol'])
            cached = self.cache.get(key)
            if cached and cached['chart_cutoff_ms'] == cutoff:
                self.cache.move_to_end(key)
                return dict(cached,checked_ms=clock)
            if start > cutoff:
                raise ValueError('交易所时间早于检测快照，暂不更新')
            rows = self.market.checkpoint(snapshot['symbol'],snapshot['timeframe'])
            raw = [[r[k] for k in ('t','o','h','l','c','v')]+[0,0,str(r.get('confirm',1))] for r in rows]
            collected = {r['t']:r for r in parse(raw,snapshot['timeframe'],cutoff) if start <= r['t'] < cutoff}
            if cached:
                for r in cached['candles']:
                    collected.setdefault(r['t'],dict(t=r['t'],o=r['open'],h=r['high'],l=r['low'],c=r['close'],v=r['volume']))
            expected = list(range(start,cutoff,step))
            source = 'existing_okx_confirmed_checkpoint'
            if any(t not in collected for t in expected):
                source = 'okx_public_candles'
                after = cutoff-step+1
                path = '/api/v5/market/candles' if cutoff-start < 1200*step else '/api/v5/market/history-candles'
                size = 300 if path.endswith('/candles') else 100
                for _ in range(MAX_TAIL//size+2):
                    batch = self.market.client.get(path,dict(instId=snapshot['symbol'],bar=snapshot['timeframe'],limit=str(size),after=str(after)))
                    if not batch:
                        break
                    for r in parse(batch,snapshot['timeframe'],cutoff):
                        if start <= r['t'] < cutoff:
                            collected[r['t']] = r
                    if all(t in collected for t in expected):
                        break
                    oldest = min(int(r[0]) for r in batch)
                    if oldest >= after:
                        raise ValueError('OKX翻页未向前推进')
                    after = oldest
                    if oldest <= start:
                        break
            if any(t not in collected for t in expected):
                raise ValueError('最新已收盘K线尚未到齐，保留上次图表，请稍后重试')
            value = dict(symbol=snapshot['symbol'],timeframe=snapshot['timeframe'],
                         detection_cutoff_ms=start,chart_cutoff_ms=cutoff,checked_ms=clock,
                         candles=extend(snapshot,[collected[t] for t in expected]),source=source)
            self.cache[key] = value
            self.cache.move_to_end(key)
            while len(self.cache) > 24:
                self.cache.popitem(last=False)
            return value
