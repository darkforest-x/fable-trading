"""Determine native Binance traded-stop and mark event order from actual minute archives."""
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.data.binance_um_vision_fetch import BASE,fetch
from yoyo.evaluation import cloud_close_binance_relative_size as transfer
from yoyo.evaluation import cloud_close_margin_before_stop as evidence

EXP = transfer.EXP.parent/'exp-cloud-close-binance-relative-minutes-20261109-v1'
CACHE = transfer.base.wallet.ROOT/'data/research/cloud_close_binance_relative_minutes'
FIELDS = ['open','high','low','close']


@lru_cache(maxsize=96)
def get_minutes(sym,hour,kind):
    symbol = sym+'USDT'
    day = pd.Timestamp(hour,unit='ms',tz='UTC').strftime('%Y-%m-%d')
    name = f'{symbol}-1m-{day}.zip'
    url = f'{BASE}/daily/{kind}/{symbol}/1m/{name}'
    status = fetch(CACHE,kind,symbol,url)[2]
    if status=='missing':
        return None,url
    if status not in ('cached','fetched'):
        raise IOError(f'Unable to establish Binance source status for {url}')
    p = CACHE/kind/symbol/name
    raw = pd.read_csv(p,header=None,usecols=[0,1,2,3,4],names=['time',*FIELDS],dtype=str)
    if raw.time.iat[0].lower()=='open_time':
        raw = raw.iloc[1:]
    f = raw.apply(pd.to_numeric).set_index('time').sort_index()
    f = f.loc[hour:hour+transfer.base.wallet.HOUR-1]
    if (not np.array_equal(f.index,hour+60000*np.arange(60)) or not np.isfinite(f).all().all() or
            not f.low.gt(0).all() or not f.low.le(f[['open','close']].min(axis=1)).all() or
            not f.high.ge(f[['open','close']].max(axis=1)).all()):
        return None,url
    return f,url


def trades(sym,hour,quotes):
    f,url = get_minutes(sym,hour,'klines')
    if f is not None:
        ts,ohlc = quotes(sym)
        i = ts.searchsorted(hour)
        agg = np.array([f.open.iat[0],f.high.max(),f.low.min(),f.close.iat[-1]])
        if ts[i]!=hour or not np.allclose(agg,ohlc[i,:4],rtol=1.e-8,atol=1.e-12):
            return None,url+' (native H1 conflict)'
    return f,url


def marks(sym,hour):
    return get_minutes(sym,hour,'markPriceKlines')[0]


def inspect(r,ratio,slip,quotes):
    return evidence.inspect(r,ratio,slip,quotes,trade_loader=trades,mark_loader=marks)


if __name__=='__main__':
    transfer.main(directory=EXP,inspect_flag=inspect)
