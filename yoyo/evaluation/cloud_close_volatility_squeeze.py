"""Price a new 12H volatility squeeze breakout in the V1.7 cloud direction."""
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_channel_entry as native
from yoyo.evaluation import cloud_close_cvc_wallet as repair

EXP = native.EXP.parent/'exp-cloud-close-volatility-squeeze-20261109-v1'
CACHE = native.CACHE.parent/'cloud_close_volatility_squeeze'


def signals(bars):
    """Closed contiguous 12H H/L/C, prior 20-bar bands and range, 180 prior bars for warmup."""
    chunks = []
    for _, block in bars.groupby(bars.index.to_series().diff().ne(native.STEP).cumsum()):
        if len(block)<=180:
            continue
        close = block.close
        previous = close.shift()
        tr = pd.concat([block.high-block.low,(block.high-previous).abs(),(block.low-previous).abs()],axis=1).max(axis=1)
        width = close.rolling(20).std(ddof=0)*2
        mean = close.rolling(20).mean()
        mid = close.ewm(span=20,min_periods=20,adjust=False).mean()
        span = 1.5*tr.rolling(20).mean()
        upper = (mean+width).shift()
        lower = (mean-width).shift()
        inside = ((mean+width <= mid+span) & (mean-width >= mid-span)).shift(fill_value=False)
        side = pd.Series(np.select([close>upper,close<lower],[1,-1],default=0),index=block.index)
        take = inside & side.ne(0) & side.ne(side.shift()) & (np.arange(len(block))>=180)
        anchor = upper.where(side>0,lower)
        chunks.append(pd.DataFrame(dict(side=side[take],anchor=anchor[take])))
    if not chunks:
        return pd.DataFrame(columns=['side','anchor'])
    return pd.concat(chunks)


if __name__=='__main__':
    quotes,src = repair.checked_quotes()
    native.run(EXP,CACHE,quotes,src,signal_builder=signals)
