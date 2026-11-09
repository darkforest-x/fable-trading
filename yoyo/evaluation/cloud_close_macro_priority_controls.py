"""Compare VIX queue timing with fixed-side preferences and a same-calendar crypto trend."""
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_vix_priority as study

queue=study.queue
EXP=queue.EXP.parent/'exp-cloud-close-macro-priority-controls-20261109-v1'


def ranks(pool):
    panel=pd.read_csv(study.regimes.EXP/'signal_regimes.csv.gz',index_col='event_id')
    panel=panel.reindex(pool.index)
    if not panel.entry_ms.equals(pool.entry_ms):
        raise ValueError('Original native VIX account and controls use different entries')
    daily=study.regimes.daily_regime(study.regimes.CACHE)
    quotes,_=queue.repair.checked_quotes()
    stamps,ohlc=quotes('BTC')
    ends=pd.Series(ohlc[:,3],index=stamps+queue.repair.wallet.HOUR)
    # Each Cboe session's end-of-UTC-date BTC H1 close. Both are known before the common D+2 gate.
    day_end=daily.available_ms-study.regimes.DAY
    btc=day_end.map(ends)
    state=np.sign(btc-btc.shift(5))
    btc_by_gate=pd.Series(state.to_numpy(),index=daily.available_ms)
    btc_direction=panel.available_ms.map(btc_by_gate)
    if btc_direction.isna().any():
        raise ValueError('Missing a native Bitcoin close at an actual comparison clock')
    out=pool.copy()
    width=pool.groupby('entry_ms').balanced.transform('max')+1
    for name,opposed in [('long_first',panel.side<0),('short_first',panel.side>0),
                         ('btc_trend',panel.side*btc_direction<0)]:
        out[name]=pool.balanced+width*opposed.astype(int)
    return out


if __name__=='__main__':
    policies=[(name,name) for name in ('long_first','short_first','btc_trend')]
    queue.main(experiment=EXP,policies=policies,score_builder=ranks)
