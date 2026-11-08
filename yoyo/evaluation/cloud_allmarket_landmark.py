"""One prespecified 12h wait: remaining chart excursion from the later close.
A secondary, separate diagnostic. Use the existing chart's exit unchanged, and
never count a wick that occurred before the delayed decision. No fills or P&L.
"""
import numpy as np
import pandas as pd
from yoyo.evaluation.cloud_allmarket_census import EXP,TOP,TOP_COINS,STEP,read_bars
from yoyo.evaluation.cloud_peak10_inputs import dump

def after_one(g,b):
    ts=b.index.to_numpy(dtype=np.int64); op=b.open.to_numpy(); hi=b.high.to_numpy()
    lo=b.low.to_numpy(); cl=b.close.to_numpy()
    nextgap=np.full(len(ts),len(ts),dtype=int)
    ends=np.r_[np.flatnonzero(np.diff(ts)!=STEP)+1,len(ts)]
    for left,right in zip(np.r_[0,ends[:-1]],ends): nextgap[left:right]=right
    out=[]
    for r in g.itertuples():
        t=int(pd.Timestamp(r.confirmed_bjt).timestamp()*1000)
        ix=int(np.searchsorted(ts,t))
        exit_t=None if pd.isna(r.path_end_bar_bjt) else int(pd.Timestamp(r.path_end_bar_bjt).timestamp()*1000)
        stop_or_flip=r.exit_reason in ('stop','cloud_flip')
        row=dict(symbol=r.symbol,week=r.week,year=int(r.confirmed_bjt[:4]),side=r.side,
            original_chart_hit=r.status=='hit',original_close_hit=r.confirmation_basis_lower_r>10,
            eligible=False,why='first_bar_missing',remaining_hit=False,remaining_possible=False,known=False,
            next24_stop=False,next24_known=False,remaining_lower_r=None,remaining_upper_r=None)
        if ix==len(ts) or ts[ix]!=t:
            out.append(row); continue
        if stop_or_flip and exit_t<t+STEP:
            row['why']=r.exit_reason;out.append(row);continue
        entry=cl[ix]; side=r.side; risk=(entry-r.reference_stop)*side
        assert risk>0
        row.update(eligible=True,why='eligible')
        left=ix+1; right=nextgap[ix]
        final=int(np.searchsorted(ts,exit_t)) if stop_or_flip else right
        assert not stop_or_flip or (left<=final<right and ts[final]==exit_t)
        fav=hi if side==1 else lo
        # On completed pre-exit bars a favorable wick is known; on the exit bar
        # only its open is certain, its favorable extreme supplies an upper bound.
        prices=fav[left:final]
        extreme=entry if not len(prices) else (max(entry,prices.max()) if side==1 else min(entry,prices.min()))
        low=max(0,(extreme-entry)*side/risk); high=low
        if stop_or_flip:
            low=max(low,(op[final]-entry)*side/risk)
            high=max(low,(fav[final]-entry)*side/risk)
        row.update(remaining_hit=low>10,remaining_possible=low<=10<high,
            known=(low>10 or (stop_or_flip and high<=10)),remaining_lower_r=low,remaining_upper_r=high,
            next24_stop=bool(r.exit_reason=='stop' and exit_t<t+3*STEP),
            next24_known=bool(stop_or_flip or right-left>=2))
        out.append(row)
    return out

def main():
    x=pd.read_csv(EXP/'signals.csv')
    x=x[~x.was_top20 & x.confirmed_bjt.str[:4].isin(['2025','2026'])]
    rows=[]
    for sym,g in x.groupby('symbol'):
        rows.extend(after_one(g,read_bars((TOP if sym in TOP_COINS else EXP)/'inputs'/sym)))
    z=pd.DataFrame(rows); out={}
    for yr,g in z.groupby('year'):
        e=g[g.eligible]
        out[str(yr)]=dict(signals=len(g),reasons=g.why.value_counts().to_dict(),observed_later_close=len(e),
            chart_hits=int(g.original_chart_hit.sum()),original_close_hits=int(g.original_close_hit.sum()),
            original_close_winners_still_present=int(e.original_close_hit.sum()),
            winners_on_both_actual_close_bases=int((e.original_close_hit & e.remaining_hit).sum()),
            remaining_gt10=int(e.remaining_hit.sum()),determinate=int(e.known.sum()),
            next24_fixed_stops=int(e.next24_stop.sum()),next24_assessable=int(e.next24_known.sum()))
    dump(EXP/'one_bar_wait.json',out)
    z.to_csv(EXP/'one_bar_wait.csv.gz',index=False)
if __name__=='__main__':
    main()
