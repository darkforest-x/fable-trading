"""Causal six-MA envelope for manual 5m tickets, using workbench semantics.

Close-price SMA/EMA 20/60/120, EMA adjust=False. Reuse the existing public
candle hub/checkpoints and the workbench's 1,000-bar warmup. The ticket chart
and stop use the same immutable closed-bar snapshot; no trading writes.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from collections import OrderedDict

from .risk import D, amount, quantize

PERIODS = (20, 60, 120)
MA_KEYS = tuple(f'{kind}{p}' for kind in ('sma', 'ema') for p in PERIODS)


def envelope_stop(snapshot, side, tick):
    """Place the stop one tick beyond the outermost MA, then round outward."""
    if side not in ('long', 'short') or D(tick) <= 0:
        raise ValueError('invalid manual exit policy')
    values = {k: D(snapshot['ma'][k]) for k in MA_KEYS}
    if min(values.values()) <= 0:
        raise ValueError('5m moving averages unavailable')
    key = min(values, key=values.get) if side == 'long' else max(values, key=values.get)
    edge = values[key]
    price = quantize(edge + (-D(tick) if side == 'long' else D(tick)), tick, up=side == 'short')
    if price <= 0:
        raise ValueError('5m moving average stop is outside valid price range')
    return dict(open_ms=snapshot['cutoff_ms']-300_000, close_ms=snapshot['cutoff_ms'],
                price=amount(price.normalize()), ma={k:amount(v) for k,v in values.items()},
                boundary_key=key, boundary_price=amount(edge), offset_ticks=1, tick=amount(tick),
                observed_ms=snapshot['observed_ms'], source='close', warmup_bars=1000)


class MovingAverages:
    """Coalesce chart/reference/preview reads for a symbol and closed cutoff."""
    def __init__(self, runtime, book=None, market=None):
        from yoyo.monitor.okx import OKX
        from yoyo.yolo_workbench.market import Market
        self.market = market or Market(OKX(runtime=runtime, priority='interactive'), runtime=runtime, book=book)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='manual-ma')
        self.lock, self.jobs = Lock(), OrderedDict()

    def read(self, symbol, cutoff):
        from yoyo.yolo_workbench.inference import enrich
        rows = self.market.candles(symbol, '5m', cutoff, cutoff-100*300_000)
        frame = enrich(rows, 'close').iloc[-100:]
        if len(frame) != 100 or int(frame.iloc[-1]['t'])+300_000 != cutoff:
            raise ValueError('5m moving averages unavailable')
        candles = []
        for _, row in frame.iterrows():
            candles.append(dict(time=int(row['t'])//1000, open=float(row['open']), high=float(row['high']),
                                low=float(row['low']), close=float(row['close']),
                                **{k:float(row[k]) for k in MA_KEYS}))
        return dict(symbol=symbol, timeframe='5m', cutoff_ms=cutoff, candles=candles,
                    ma={k:str(frame.iloc[-1][k]) for k in MA_KEYS}, warmup_bars=1000, ma_source='close')

    def get(self, symbol, clock):
        cutoff = clock()//300_000*300_000
        key = symbol, cutoff
        with self.lock:
            job = self.jobs.get(key)
            if job is None:
                job = self.jobs[key] = self.pool.submit(self.read, symbol, cutoff)
                while len(self.jobs)>32:
                    self.jobs.popitem(last=False)
        try:
            snapshot = job.result()
        except Exception:
            with self.lock:
                if self.jobs.get(key) is job:
                    self.jobs.pop(key)
            raise
        if clock()//300_000*300_000 != cutoff:
            raise ValueError('latest closed 5m candle unavailable')
        return dict(snapshot, observed_ms=clock())

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
