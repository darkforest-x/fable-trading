"""Observe a small-timeframe breakout and first retest after a closed hourly directional wick.

Both 5m and 15m start at the same H1 close. Each uses the first local favorable
extreme above/below the monthly rail formed by three completed post-signal bars.
It becomes known at the right neighbor's close. A later close must break it.
The first subsequent bar reaching that price from the favorable side decides
held vs failed by its close. Record the next open and fixed-horizon OHLC moves.
No stop, target, capital allocation or outcome-dependent sample selection.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
from functools import lru_cache
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / 'exp-alt-isolated-12h-firstweek-wicks-20261111-v1'))
from census12 import HOUR, read_raw
from scan import CUTOFF, bounds
from bars import load_bars

FIVE = HOUR // 12
DAY = 24 * HOUR
BASE = ROOT / 'data/research/alt-isolated-12h-wicks-20261111/expanded12/all_replay_v1'
OUT = BASE / 'intraday_retest'


@lru_cache(maxsize=None)
def expiry(month):
    return bounds(month)[2].value // 1_000_000


def summarize_bars(raw, n):
    blocks = raw[:len(raw)//n*n].reshape(-1,n,5)
    return np.column_stack((blocks[:,0,0], blocks[:,0,1], blocks[:,:,2].max(axis=1),
                            blocks[:,:,3].min(axis=1), blocks[:,-1,4]))


def read_pattern(bars, minutes, r, hour_close, stop):
    sign = 1 if r.side == 'long' else -1
    step = minutes*60_000
    known = r.touch_ms+HOUR
    # A close at expiry or the study cutoff cannot initiate the watch's trade.
    # At the 24h observation boundary, accept a qualifying close if the rail is live.
    decisions = bars[(bars[:,0]+step <= stop) & (bars[:,0]+step < expiry(r.month))]
    out = dict(minutes=minutes, state='no_pivot')
    extreme = sign*decisions[:,2 if sign == 1 else 3]
    pivot_candidates = np.flatnonzero((extreme[1:-1]>extreme[:-2]) &
                                      (extreme[1:-1]>extreme[2:]) &
                                      (extreme[1:-1]>sign*r.rail))+1
    if not len(pivot_candidates):
        return out
    pivot = pivot_candidates[0]
    level = extreme[pivot]
    out.update(state='no_break',pivot_price=sign*level,pivot_known_ms=decisions[pivot+1,0]+step)
    ix = np.flatnonzero(sign*decisions[pivot+2:,4] > level)
    if not len(ix):
        return out
    breakout = pivot+2+ix[0]
    out.update(state='no_retest',break_known_ms=decisions[breakout,0]+step)
    adverse = sign*decisions[breakout+1:,3 if sign==1 else 2]
    ix = np.flatnonzero(adverse<=level)
    if not len(ix):
        return out
    retest = breakout+1+ix[0]
    bar = decisions[retest]
    state = 'held' if sign*bar[4]>level else 'failed'
    at = retest+1
    entry = bars[at,1]
    assert bars[at,0] == bar[0]+step and known <= out['pivot_known_ms'] < out['break_known_ms'] < bars[at,0]
    out.update(state=state,read_ms=int(bars[at,0]),ref_open=entry,
               delay_min=(bars[at,0]-known)/60_000,
               move_before_pct=100*sign*(entry/hour_close-1),
               room_to_edge_pct=100*sign*(r.near_edge/entry-1))
    for hours in (4,24):
        n = hours*60//minutes
        window = bars[at:at+n]
        assert len(window)==n and window[-1,0]+step==bars[at,0]+hours*HOUR
        best = window[:,2].max() if sign==1 else window[:,3].min()
        worst = window[:,3].min() if sign==1 else window[:,2].max()
        out[f'close{hours}']=100*sign*(window[-1,4]/entry-1)
        out[f'favorable{hours}']=max(0.,100*sign*(best/entry-1))
        out[f'adverse{hours}']=min(0.,100*sign*(worst/entry-1))
    return out


def run_symbol(item):
    symbol, source = item
    hourly = read_raw(symbol)
    times = hourly[:,0].astype('int64')
    cache = lru_cache(maxsize=4)(lambda m: load_bars(symbol,m))
    rows = []
    for r in source.sort_values('touch_ms').itertuples():
        i = times.searchsorted(r.touch_ms)
        assert times[i]==r.touch_ms
        o,h,l,c = hourly[i,1:5]
        is_long = r.side=='long'
        wick = min(o,c)-l if is_long else h-max(o,c)
        inside = min(o,c)>r.rail if is_long else max(o,c)<r.rail
        if not inside or h<=l or wick/(h-l)<.5:
            continue
        known = r.touch_ms+HOUR
        stop = min(known+DAY, expiry(r.month))
        end = stop+DAY
        common = dict(id=r.id,symbol=symbol,month=r.month,side=r.side,touch_ms=r.touch_ms,
                      direction_close=c,rail=r.rail,near_edge=r.near_edge)
        if known>=expiry(r.month):
            rows.append(dict(**common,coverage='rail_expired'))
            continue
        if end>CUTOFF:
            rows.append(dict(**common,coverage='end_of_study'))
            continue
        first = pd.Timestamp(r.touch_ms,unit='ms').to_period('M')
        last = pd.Timestamp(end-1,unit='ms').to_period('M')
        parts = []
        for month in pd.period_range(first,last,freq='M'):
            a = cache(str(month))
            lo,hi = a[:,0].searchsorted([r.touch_ms,end])
            parts.append(a[lo:hi])
        tape = np.concatenate(parts)
        if (len(tape)!=(end-r.touch_ms)//FIVE or
                not np.array_equal(tape[:,0],np.arange(r.touch_ms,end,FIVE))):
            rows.append(dict(**common,coverage='incomplete_5m'))
            continue
        needle = summarize_bars(tape[:12],12)[0,1:]
        if not np.allclose(needle,[o,h,l,c],atol=0,rtol=1e-8):
            rows.append(dict(**common,coverage='source_disagrees_on_h1'))
            continue
        first_day=tape[12:12+DAY//FIVE]
        direction=1 if is_long else -1
        excursion=100*direction*(first_day[:,2 if is_long else 3]/c-1)
        for threshold in (10,20):
            hits=np.flatnonzero(excursion>=threshold)
            common[f'first_{threshold}_bar_ms']=first_day[hits[0],0] if len(hits) else np.nan
        for minutes in (5,15):
            bars = tape[12:] if minutes==5 else summarize_bars(tape[12:],3)
            rows.append(dict(**common,coverage='complete',**read_pattern(bars,minutes,r,c,stop)))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--retry-missing', action='store_true')
    args = parser.parse_args()
    frame = pd.read_csv(BASE/'cards.csv.gz',float_precision='round_trip')
    assert len(frame)==frame.id.nunique()==50986
    path=OUT/'events.csv.gz'
    retained = pd.DataFrame()
    if args.retry_missing:
        existing=pd.read_csv(path,float_precision='round_trip')
        missing=existing.coverage.eq('incomplete_5m')
        retained=existing[~missing]
        frame=frame[frame.id.isin(existing.loc[missing,'id'])]
    rows = []
    with ThreadPoolExecutor(max_workers=6) as workers:
        for n,part in enumerate(workers.map(run_symbol,frame.groupby('symbol')), start=1):
            rows.extend(part)
            if n%100==0:
                print('contracts processed', n, 'rows', len(rows), flush=True)
    events = pd.concat([retained,pd.DataFrame(rows)],ignore_index=True)
    assert events.id.nunique()==7777
    paired = events[events.coverage=='complete']
    assert paired.groupby('id').minutes.nunique().eq(2).all()
    OUT.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    events.to_csv(tmp,index=False,compression='gzip')
    tmp.replace(path)
    print(events.drop_duplicates('id').coverage.value_counts().to_dict())
    print(paired.groupby(['minutes','state']).size())


if __name__=='__main__':
    main()
