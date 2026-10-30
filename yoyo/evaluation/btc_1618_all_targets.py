"""Extend the owner's frozen859 touch observations to three same-week targets.

2026-10-30 owner clarification: nearest Monday edge, far Monday edge, then
opposite1.618. Keep the original events, NY expiry and noon cutoff. This is
descriptive price reachability, without orders, stops or PnL. Reuse the
original15m/1m ordering proof and public5m snapshots for display timestamps.
"""
from __future__ import annotations
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.btc_1618_return_count import END, EXP, load, same_bar_bounds, ordered_same_bar

DEST = EXP/'full_gallery'
NAMES = ['near', 'far', 'opposite1618']


def marker_time(fine, touch, bar, side, level, edge):
    """Find a chronologically proved reach in an already accepted target bar."""
    candidates = fine[(fine.index >= bar) & (fine.index < bar+pd.Timedelta(minutes=15))]
    candidates = candidates[candidates.index >= touch]
    reach = candidates.low <= edge if side == 'upper' else candidates.high >= edge
    for stamp, candle in candidates[reach].iterrows():
        if stamp > touch or ordered_same_bar(candle, side, level, edge):
            return stamp
    return None


def prepare():
    DEST.mkdir(parents=True, exist_ok=True)
    fifteen, minute, _ = load()
    five = minute.resample('5min').agg(dict(open='first', high='max', low='min', close='last'))
    assert minute.close.resample('5min').count().eq(5).all()
    latest = pd.read_csv(EXP/'charts/inputs_5m.csv.gz')
    latest.index = pd.to_datetime(latest.ts.astype('int64'), unit='ms', utc=True)
    latest = latest[['open','high','low','close']].astype(float)
    overlap = five.index.intersection(latest.index)
    assert np.allclose(five.loc[overlap], latest.loc[overlap], atol=1e-8, rtol=0)
    five = pd.concat([five, latest[latest.index > five.index[-1]]])
    five = five[five.index < END]
    assert five.index[-1]+pd.Timedelta(minutes=5) == END
    assert five.index.is_unique and ((five.index[1:]-five.index[:-1]) == pd.Timedelta(minutes=5)).all()
    grouped = five.resample('15min').agg(dict(open='first', high='max', low='min', close='last'))
    assert np.allclose(grouped.loc[fifteen.index], fifteen, rtol=0, atol=1e-8)
    five.to_csv(DEST/'inputs_5m.csv.gz', index_label='time')
    fifteen.to_csv(DEST/'inputs_15m.csv.gz', index_label='time')
    events = pd.read_csv(EXP/'events.csv')
    rows=[]
    for number, original in enumerate(events.to_dict('records'), 1):
        t=pd.Timestamp(original['start']);side=original['side'];level=original['level'];edge=original['edge']
        week=pd.Timestamp(original['week']).tz_localize('America/New_York')
        expiry=(pd.Timestamp(original['week'])+pd.Timedelta(days=7)).tz_localize('America/New_York')
        monday=fifteen[(fifteen.index>=week)&(fifteen.index<week+pd.Timedelta(days=1))]
        assert len(monday)==96
        high,low=float(monday.high.max()),float(monday.low.min());width=high-low
        targets=[edge, low if side=='upper' else high, low-.618*width if side=='upper' else high+.618*width]
        fine=minute[(minute.index>=t)&(minute.index<t+pd.Timedelta(minutes=15))]
        resolution=1
        if len(fine)!=15:
            fine=five[(five.index>=t)&(five.index<t+pd.Timedelta(minutes=15))];resolution=5
        first=fine[(fine.low<=level)&(fine.high>=level)].index[0]
        i=int(fifteen.index.get_loc(t));end=min(expiry,END)
        future=fifteen[(fifteen.index>t)&(fifteen.index<end)]
        row=dict(original,number=number,event_id=f'{number:04d}',monday_high=high,monday_low=low,
                 touch_5m=str(first.floor('5min')),touch_source_minutes=resolution,
                 expiry=str(expiry),observation_end=str(end))
        for name,target in zip(NAMES,targets):
            certain,possible=same_bar_bounds(fifteen,minute,i,side,level,target)
            later=future[future.low<=target] if side=='upper' else future[future.high>=target]
            bar=t if certain else later.index[0] if len(later) else None
            status='returned' if bar is not None else 'ambiguous' if possible else 'pending' if expiry>END else 'not_returned'
            row[name+'_price']=float(target);row[name+'_status']=status;row[name+'_15m']=str(bar) if bar is not None else None
            mark=None
            if bar is not None:
                source=minute if bar+pd.Timedelta(minutes=15)<=minute.index[-1]+pd.Timedelta(minutes=1) else five
                fine_touch=first if source is minute else first.floor('5min')
                refined=marker_time(source,fine_touch,bar,side,level,target)
                assert refined is not None,(number,name,'missing ordered marker')
                mark=refined.floor('5min')
                assert mark.floor('15min')==bar
            row[name+'_5m']=str(mark) if mark is not None else None
        assert row['near_status']==original['same_week'],(number,'near status drift')
        if row['near_status']=='returned':
            assert pd.Timestamp(row['near_15m'])==pd.Timestamp(original['return_bar'])
        returned=[row[name+'_status']=='returned' for name in NAMES]
        assert returned==sorted(returned,reverse=True)
        rows.append(row)
    frame=pd.DataFrame(rows)
    assert len(frame)==859 and frame.side.value_counts().to_dict()=={'lower':451,'upper':408}
    frame.to_csv(DEST/'events_targets.csv',index=False)
    summary=dict(events=859,data_end=str(END),deadline='Next NY Monday, never cross weeks.',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        original_near_labels_identical=True,overlap_5m_bars_identical=len(overlap),all_5m_aggregate_to_original_15m=True,
        tables={name:{side:dict(n=len(g),**g[name+'_status'].value_counts().to_dict()) for side,g in frame.groupby('side')} for name in NAMES},
        training_eligible=False,production_eligible=False)
    (DEST/'targets_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    prepare()
