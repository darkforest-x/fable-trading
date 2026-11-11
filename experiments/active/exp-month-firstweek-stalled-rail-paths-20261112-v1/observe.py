"""Describe the frozen H1 non-advancers (including unchanged closes); no trades.

At each of hours 2, 3, 4, inspect only complete native Binance H1 OHLCs from
the first 12h reclaim close up to that checkpoint, never the hour deciding the
original +20/-5 label. Carry the state of hour 1 into hour 2. A close strictly
outside loses the rail; the first subsequent strictly inside close recovers
it. Equality is a separate price state and triggers neither transition. Compare
prices with a 1e-12 relative floating arithmetic tolerance solely for equality,
as in the original barrier census. High/low touches in hours 2..h are recorded
separately. Also count recoveries already observed in shorter, censored paths.
"""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'exp-month-firstweek-all-replay-20261111-v1'))
from replay import HOUR, OUT, ROUNDTRIP, read_raw

INPUT = OUT / 'first_reclaim_hours' / 'landmarks.csv.gz'
OUTPUT = OUT / 'stalled_rail_paths'
SIDE = {'long':'下轨', 'short':'上轨'}
GOOD, BAD = '先有利20', '先不利5'
HELD, BACK, LOST, AGAIN = '收盘守住', '失守后现已收回', '失守未曾收回', '收回后再失守'
EDGE, DONE, EXPIRED = '当根收在线上', '已触及结果', '原轨到期'
STATES = [HELD, BACK, LOST, AGAIN, EDGE, DONE, EXPIRED]


def price_state(close, rail, sign):
    delta = sign * (close - rail)
    return 0 if abs(delta) <= rail*1e-12 else (1 if delta>0 else -1)


def inspect(path, raw):
    """Inputs: frozen H1 closes/status at h=1..4, raw H1 low/high/close up to last eligible checkpoint."""
    first = path.iloc[0]
    assert list(path.hour)==[1,2,3,4] and first.status=='可观察'
    first_idx = int(np.searchsorted(raw[:,0], first.t0))
    rail, sign = float(first.rail), (1 if first.side=='long' else -1)
    first_loss = first_back = first_reloss = first_touch = first_cross = 0
    h1_state = None
    for rec in path.itertuples():
        h=rec.hour
        state=None
        if rec.status=='可观察':
            ts, _, high, low, close = raw[first_idx+h-1,:5]
            assert ts+HOUR==rec.when and rec.when<=rec.result_hour and not rec.expired
            assert close==rec.close and low<=close<=high
            state=price_state(close,rail,sign)
            adverse_extreme=price_state(low if sign==1 else high,rail,sign)
            if h>1 and adverse_extreme<=0 and not first_touch:
                first_touch=h
            if h>1 and adverse_extreme<0 and not first_cross:
                first_cross=h
            if state<0 and not first_loss:
                first_loss=h
            if state>0 and first_loss and not first_back:
                first_back=h
            if state<0 and first_back and not first_reloss:
                first_reloss=h
            label=(EDGE if state==0 else (BACK if first_loss else HELD) if state>0
                   else AGAIN if first_back else LOST)
        else:
            assert pd.isna(rec.close) and rec.status in (DONE,EXPIRED)
            label=rec.status
        if h==1:
            h1_state=state
            continue
        yield dict(id=rec.id,symbol=rec.symbol,month=rec.month,side=SIDE[rec.side],
                   outcome=rec.outcome,hour=h,when=rec.when,rail=rail,h1_state=h1_state,
                   current_state=state,path=label,first_loss=first_loss,first_back=first_back,
                   first_reloss=first_reloss,first_touch_since_h2=first_touch,
                   first_cross_since_h2=first_cross)


def main():
    data=pd.read_csv(INPUT,**ROUNDTRIP)
    h1=data[(data.hour==1)&(data.status=='可观察')&data.farther.eq(False)]
    assert h1.id.is_unique and h1.outcome.value_counts().to_dict()=={GOOD:660,BAD:1854}
    assert h1.delta_pp.le(0).all()
    selected=data[data.id.isin(h1.id)&data.hour.between(1,4)].sort_values(['symbol','id','hour'])
    rows=[]
    for symbol,batch in selected.groupby('symbol',sort=False):
        raw=read_raw(symbol)
        for _,path in batch.groupby('id',sort=False):
            rows.extend(inspect(path,raw))
    frame=pd.DataFrame(rows)
    assert len(frame)==3*len(h1) and not frame.duplicated(['id','hour']).any()
    totals=[]
    for (s,g,h),a in frame.groupby(['side','outcome','hour']):
        counts=a.path.value_counts().reindex(STATES,fill_value=0).to_dict()
        assert sum(counts.values())==len(a)
        held=a.path.eq(HELD)
        totals.append(dict(轨道=s,结局=g,截至第几根1H=h,原组=len(a),**counts,
                           首小时收在线外=a.h1_state.eq(-1).sum(),首小时收在线上=a.h1_state.eq(0).sum(),
                           守住且期间盘中未碰线=(held&a.first_touch_since_h2.eq(0)).sum(),
                           守住且期间盘中触线未穿=(held&a.first_touch_since_h2.gt(0)&a.first_cross_since_h2.eq(0)).sum(),
                           守住且期间盘中穿线=(held&a.first_cross_since_h2.gt(0)).sum(),
                           期间首次收回含后来已出结果=a.first_back.gt(0).sum(),
                           已出结果前曾收回=(a.path.isin([DONE,EXPIRED])&a.first_back.gt(0)).sum(),
                           可见的收回中首小时已失守=(a.path.eq(BACK)&a.h1_state.eq(-1)).sum()))
    stats=pd.DataFrame(totals)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    frame.to_csv(OUTPUT/'paths.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    stats.to_csv(HERE/'counts.csv',index=False,encoding='utf-8-sig')
    print(stats.to_string(index=False))


if __name__=='__main__':
    main()
