"""Display-only closed 5m OHLC for the owner's manual risk/reward chart.

Reads public OHLC through the shared candle hub. No synthetic future candles,
account data, detection promotion or order writes. Chart loading is independent
of the frozen 60-second order preview and never extends its expiry.
"""
from concurrent.futures import ThreadPoolExecutor
import time
from yoyo.yolo_workbench.market import parse
from .engine import one
from .manual_ma import envelope_stop
from .manual import reference_prices, PREVIEW_TTL_MS


def reference(client, context, rr, stop, clock, *, ma_snapshot):
    """Read public prices before private eligibility; never create a queue row."""
    if type(rr) is not int or rr not in (3, 5):
        raise ValueError('invalid manual exit policy')
    class Reads:
        def get(self, path, **query): return client.get(path, query)
    reads = Reads()
    with ThreadPoolExecutor(max_workers=2) as pool:
        candle_job = pool.submit(ma_snapshot, context['symbol']) if stop is None else None
        instrument_job = pool.submit(reads.get, '/api/v5/public/instruments', instType='SWAP', instId=context['symbol'])
        candle, instrument = candle_job.result() if candle_job else None, one(instrument_job.result())
    if (instrument.get('instId') != context['symbol'] or instrument.get('state') != 'live'
            or instrument.get('ctType') != 'linear' or instrument.get('settleCcy') != 'USDT'):
        raise ValueError('instrument is not an available live linear USDT swap')
    if candle is not None:
        candle = envelope_stop(candle, context['proposal_side'], instrument['tickSz'])
    ticker = one(reads.get('/api/v5/market/ticker', instId=context['symbol']))
    observed = clock()
    ahead = int(ticker['ts']) - observed
    if 0 < ahead <= 5000:
        time.sleep((ahead+1)/1000)
        observed = clock()
    if not 0 <= observed-int(ticker['ts']) <= 5000:
        raise ValueError('manual preview or quote expired')
    return reference_prices(symbol=context['symbol'], timeframe=context['timeframe'], side=context['proposal_side'],
                            rr=rr, price=ticker['askPx' if context['proposal_side']=='long' else 'bidPx'],
                            stop=stop if stop is not None else candle['price'], tick=instrument['tickSz'],
                            candle=candle, stop_source='manual' if stop is not None else 'ma_envelope_5m',
                            quote_ms=int(ticker['ts']), observed_ms=observed, expires_ms=observed+PREVIEW_TTL_MS)


def candles(client, symbol, clock):
    cutoff = clock() // 300_000 * 300_000
    raw = client.get('/api/v5/market/candles', {'instId': symbol, 'bar': '5m', 'limit': '100'})
    rows = sorted(parse(raw, '5m', cutoff), key=lambda r: r['t'])
    if (not rows or rows[-1]['t'] + 300_000 != cutoff
            or any(b['t']-a['t'] != 300_000 for a, b in zip(rows, rows[1:]))):
        raise ValueError('图表的最新已收盘 K 线尚未到齐，请重试')
    return dict(symbol=symbol, timeframe='5m', cutoff_ms=cutoff, candles=[
        dict(time=r['t']//1000, open=r['o'], high=r['h'], low=r['l'], close=r['c']) for r in rows])
