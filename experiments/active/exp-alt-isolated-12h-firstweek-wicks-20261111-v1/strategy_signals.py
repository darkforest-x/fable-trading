"""Causal 12h setups on all frozen Binance perpetual listings.

The NY first-week high/low use only its completed H1 bars. Touch rank, body
reclaim and wick fraction use OHLC through the signal close. The following
hour's open is the execution reference. Later contacts, extrema and the
eventual reversal outcome are never used to construct signals.
"""
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from all_contract_touches import DEST
from census12 import BASE, HOUR, read_raw
from scan import CAL, CUTOFF, SLOT

BAR = 12 * HOUR
WINDOWS = [(m, s*SLOT, r*SLOT, e*SLOT) for m,s,r,e in CAL]
OUT = DEST / 'strategy_v1'
COLUMNS = ['symbol','month','side','entry_ms','entry_price','rail','baseline','first_wick','retest',
           'touch_ms','rank_12h','body_inside','wick_fraction','tip_outside_pct','close_to_rail_pct']


def one_symbol(symbol):
    q = read_raw(symbol)
    q = q[q[:,0] + HOUR <= CUTOFF]
    if not len(q):
        return []
    t = q[:,0].astype('int64')
    first_day_seen = set()
    rows = []
    for month, start, ready, end in WINDOWS:
        if ready < t[0] or ready >= t[-1]:
            continue
        i,j,k = t.searchsorted([start,ready,end])
        if j-i != (ready-start)//HOUR:
            continue
        h,l = q[i:j,2].max(),q[i:j,3].min()
        if h <= l:
            continue
        # After a missing hour the ordinal is unknown. Keep every earlier
        # decision; a later data gap cannot retroactively remove a signal.
        missing = np.flatnonzero(t[j:k] != ready + np.arange(k-j)*HOUR)
        if len(missing):
            k = j+int(missing[0])
        hi,lo = h+.618*(h-l), l-.618*(h-l)
        active = q[j:k]
        lower = (lo>0) & (active[:,3]<=lo) & (active[:,2]>=lo)
        upper = (active[:,3]<=hi) & (active[:,2]>=hi)
        hits = np.flatnonzero(lower|upper)
        if not len(hits):
            continue
        days = (t[j+hits]+8*HOUR)//(24*HOUR)
        first_today = set()
        for day, pos in zip(*np.unique(days,return_index=True)):
            b = hits[pos]
            if day not in first_day_seen and not (lower[b] and upper[b]):
                first_today.add(int(j+b))
            first_day_seen.add(int(day))
        for side,mask,rail,sign in [('long',lower,lo,1),('short',upper,hi,-1)]:
            ix = np.flatnonzero(mask)+j
            bar_times,offsets = np.unique(t[ix]//BAR*BAR,return_index=True)
            first_body_inside = False
            for rank,(bar,offset) in enumerate(zip(bar_times,offsets),start=1):
                close = bar+BAR
                at,after = t.searchsorted([bar,close])
                if close >= end or after >= len(q) or t[after]!=close or after-at!=12:
                    continue
                b = q[at:after]
                opening,closing = b[0,1],b[-1,4]
                high,low = b[:,2].max(),b[:,3].min()
                inside = min(sign*(opening-rail),sign*(closing-rail))>0
                wick = min(opening,closing)-low if sign==1 else high-max(opening,closing)
                share = wick/(high-low) if high>low else 0.
                if rank==1:
                    first_body_inside = inside
                last_offset = offsets[rank] if rank<len(offsets) else len(ix)
                eligible = next((n for n in ix[offset:last_offset] if n in first_today),None)
                if eligible is None:
                    continue
                single = rank==1 and inside and share>=.5
                second = rank==2 and first_body_inside and inside and bar-bar_times[0]>=2*BAR
                tip = low if sign==1 else high
                rows.append([symbol,month,side,int(close),q[after,1],rail,1,int(single),int(second),
                             int(t[eligible]),rank,int(inside),share,sign*(1-tip/rail)*100,
                             sign*(closing/rail-1)*100])
    return rows


def main():
    universe = pd.read_csv(BASE/'universe_perpetuals.csv')
    with ThreadPoolExecutor(max_workers=5) as pool:
        rows = [r for batch in pool.map(one_symbol,universe.symbol) for r in batch]
    frame = pd.DataFrame(rows,columns=COLUMNS).sort_values(['entry_ms','symbol','side'])
    assert not frame.duplicated(['symbol','entry_ms','side']).any()
    day = (frame.touch_ms+8*HOUR)//(24*HOUR)
    assert not frame.assign(day=day).duplicated(['symbol','day']).any()
    assert ((frame.entry_ms>frame.touch_ms)&(frame.entry_ms-frame.touch_ms<=BAR)).all()
    OUT.mkdir(parents=True,exist_ok=True)
    tmp = OUT/'signals.tmp.gz'
    frame.to_csv(tmp,index=False,compression='gzip')
    tmp.replace(OUT/'signals.csv.gz')
    print(frame[['baseline','first_wick','retest']].sum().to_dict(),frame.symbol.nunique(),flush=True)


if __name__=='__main__':
    main()
