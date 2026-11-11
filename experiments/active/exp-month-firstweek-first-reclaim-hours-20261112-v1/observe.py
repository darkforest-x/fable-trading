"""Describe 0–6 fully closed H1 candles following a first-touch 12h reclaim.

Stage 0 is the native 12h close. At h>0 the most recent price is the close
of the source H1 OPENING at t0+(h-1)*HOUR. Only the original unresolved groups
at t0 are included. The deciding hour OPEN is the last observable checkpoint
for each case; data in that hour cannot enter its decision features. Changing
risk sets and their original denominators are retained at every checkpoint.
"""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent / 'exp-month-firstweek-all-replay-20261111-v1'
sys.path.insert(0, str(PARENT))
from direction_check import match
from replay import HOUR, OUT, WINDOW, ROUNDTRIP, read_raw

DEST = OUT / 'first_reclaim_hours'
MAX_HOUR = 6
LABEL = {'确认TP先于SL':'先有利20','确认SL先于TP':'先不利5'}
GOOD, BAD = LABEL.values()
SCOPES = ['全部','2025年以前','2025年起','下轨','上轨']
BOOTSTRAPS = 4000


def select_cases():
    archive = match()
    first = archive.first_close_state.isin(['实体在线内且长影','收回'])
    on_time = archive.scheduled_close_ms.le(archive.result_hour)
    rows = archive[first & on_time & archive['判定'].isin(LABEL)].copy()
    rows['outcome'] = rows['判定'].map(LABEL)
    assert rows.outcome.value_counts().to_dict()=={GOOD:1616,BAD:3722}
    assert rows.id.is_unique
    return rows


def prices(raw, t0, rail, sign, last_known):
    """Read only H1 closes from t0-1h to last_known, including no currently open H1."""
    start = t0 - HOUR
    first, past = raw[:,0].searchsorted([start,last_known])
    hours = raw[first:past]
    assert np.array_equal(hours[:,0], np.arange(start,last_known,HOUR))
    closes = hours[:,4]
    assert np.all(np.isfinite(closes)) and np.all(closes>0)
    distance = sign * (closes/rail - 1) * 100
    delta = sign * (closes - closes[0]) / rail * 100
    move = sign * (closes/closes[0] - 1) * 100
    return closes, distance, delta, move


def build():
    rows = select_cases()
    data = []
    for symbol, group in rows.groupby('symbol'):
        raw = read_raw(symbol)
        for row in group.itertuples():
            t0 = int(row.scheduled_close_ms)
            result = int(row.result_hour)
            expiry = WINDOW[row.month][2]
            sign = 1 if row.side=='long' else -1
            last_known = min(result,expiry,t0+MAX_HOUR*HOUR)
            close, distance, delta, move = prices(raw,t0,row.rail,sign,last_known)
            assert np.isclose(distance[0],row.close_pct,atol=1e-8) and distance[0]>0
            for h in range(MAX_HOUR+1):
                when = t0+h*HOUR
                status = '已触及结果' if when>result else ('原轨到期' if when>expiry else '可观察')
                obs=dict(id=row.id, symbol=symbol, month=row.month, side=row.side, t0=t0,
                         hour=h, when=when, outcome=row.outcome, result_hour=result,
                         expired=when>expiry, resolved=when>result, status=status,
                         rail=row.rail, start_close=close[0], start_distance_pct=distance[0])
                if status=='可观察':
                    obs.update(close=close[h], distance_pct=distance[h], delta_pp=delta[h],
                               move_pct=move[h], farther=close[h]*sign>close[0]*sign)
                data.append(obs)
    data=pd.DataFrame(data)
    assert len(data)==len(rows)*(MAX_HOUR+1)
    DEST.mkdir(exist_ok=True,parents=True)
    data.to_csv(DEST/'landmarks.csv.gz',index=False,compression={'method':'gzip','compresslevel':4,'mtime':0})
    return data


def auc(labels, score):
    """Probability a random positive has a higher score than a random adverse case, ties half credit."""
    pos=labels.eq(GOOD)
    n=int(pos.sum())
    return (score.rank().loc[pos].sum()-n*(n+1)/2)/(n*(len(score)-n))


def describe(data):
    records=[]
    measures=[]
    subsets=[data,data[data.month<'2025-01'],data[data.month>='2025-01'],
             data[data.side=='long'],data[data.side=='short']]
    for name,subset in zip(SCOPES,subsets):
        for h,both in subset.groupby('hour'):
            active=both[both.status.eq('可观察')]
            scores=dict(时期或方向=name,小时=h,距轨AUC=auc(active.outcome,active.distance_pct),
                        同批起点距轨AUC=auc(active.outcome,active.start_distance_pct),
                        较起点位移AUC=auc(active.outcome,active.delta_pp))
            rates={}
            for label,all_rows in both.groupby('outcome'):
                a=active[active.outcome.eq(label)]
                rates[label]=a.farther.eq(True).mean()
                records.append(dict(时期或方向=name,小时=h,组别=label,原组=len(all_rows),仍可观察=len(a),
                       已有结果=int(all_rows.resolved.sum()),
                       未有结果但轨过期=int((~all_rows.resolved & all_rows.expired).sum()),
                       更远数=int(a.farther.eq(True).sum()),比起点更远占比=rates[label],
                       距轨中位数百分比=a.distance_pct.median(),距轨P25=a.distance_pct.quantile(.25),距轨P75=a.distance_pct.quantile(.75),
                       比起点多走中位数百分点=a.delta_pp.median(),位移P25=a.delta_pp.quantile(.25),位移P75=a.delta_pp.quantile(.75),
                       以起点收盘价计算的有利变动百分比中位数=a.move_pct.median(),
                       同批起点收盘距轨中位数百分比=a.start_distance_pct.median()))
            scores['更远比例差百分点']=100*(rates[GOOD]-rates[BAD])
            measures.append(scores)
    return pd.DataFrame(records),pd.DataFrame(measures)


def month_bands(data,measures):
    """Simultaneous uncertainty across h=1..6 by resampling NY range months, preserving co-moving symbols."""
    months=sorted(data.month.unique())
    rng=np.random.default_rng(20261112)
    counts=rng.multinomial(len(months),np.full(len(months),1/len(months)),size=BOOTSTRAPS)
    active=data[(data.hour>0)&data.status.eq('可观察')].copy()
    active['above']=active.farther.eq(True)
    rates={}
    for label in [GOOD,BAD]:
        subset=active[active.outcome.eq(label)]
        values=subset.groupby(['month','hour']).above.agg(['sum','size'])
        arrays=[values[k].unstack().reindex(months).fillna(0).to_numpy(dtype=np.int64) for k in ['sum','size']]
        numerator,denominator=(counts@a for a in arrays)
        assert ((0 <= numerator) & (numerator <= denominator) & (denominator > 0)).all()
        rates[label]=numerator/denominator
    diff=(rates[GOOD]-rates[BAD])*100
    pooled=(measures['时期或方向']=='全部')&(measures['小时']>0)
    point=measures.loc[pooled,'更远比例差百分点'].to_numpy()
    radius=np.quantile(np.max(np.abs(diff-point),axis=1),.95)
    measures.loc[pooled,'六时点同时95下界']=point-radius
    measures.loc[pooled,'六时点同时95上界']=point+radius


if __name__=='__main__':
    data=build()
    profile,measures=describe(data)
    month_bands(data,measures)
    profile.to_csv(HERE/'hourly_groups.csv',index=False,encoding='utf-8-sig')
    measures.to_csv(HERE/'hourly_separation.csv',index=False,encoding='utf-8-sig')
    print(profile[profile['时期或方向']=='全部'].round(2).to_string(index=False))
    print(measures[measures['时期或方向']=='全部'].round(3).to_string(index=False))
