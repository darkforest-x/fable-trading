"""Separate, fixed before-touch checks for the original 12H cloud alerts.
Features consume only OHLC/EMA strictly before the key 12H bar, plus earlier
completed original alerts in the current same-direction contiguous cloud run.
All historical data are frozen; no parameter search or trade simulation.
"""
import json
import numpy as np
import pandas as pd
from yoyo.evaluation.cloud_allmarket_census import EXP as SOURCE, read_bars, STEP, ROOT
from yoyo.monitor.cloud_signals import ema_points
from yoyo.evaluation.cloud_peak10_inputs import dump

EXP=ROOT/'experiments/active/exp-cloud-pretouch-structure-20261108-v1'
RULES=['first_in_cloud','few_cloud_traversals','directional_highs_lows']

def features(g,bars):
    ema=pd.DataFrame(ema_points(bars[['close']].reset_index().to_dict('records')))
    direction=np.sign(ema.ema20-ema.ema50).where(ema.ready,0).to_numpy()
    run=np.cumsum(np.r_[True,(direction[1:]!=direction[:-1]) | (np.diff(bars.index)!=STEP)])
    outside=np.where(bars.close.values>np.maximum(ema.ema20,ema.ema50),1,
                     np.where(bars.close.values<np.minimum(ema.ema20,ema.ema50),-1,0))
    high,low=bars.high.to_numpy(),bars.low.to_numpy()
    count=ema['count'].to_numpy(); seen=set(); output=[]
    for r in g.sort_values('confirmed_bjt').itertuples():
        key=int(pd.Timestamp(r.key_open_bjt).timestamp()*1000)
        i=bars.index.get_loc(key); side=r.side
        first=crosses=structure=None
        if i>0 and count[i-1]>=50 and bars.index[i]-bars.index[i-1]==STEP:
            first=bool(direction[i-1]==side and (run[i-1],side) not in seen)
        if i>=14 and count[i]>=64:
            prior=outside[i-14:i]; states=prior[prior!=0]
            crosses=int(np.count_nonzero(np.diff(states)))
        if i>=28 and count[i]>=29:
            high_change=np.sign(high[i-14:i].max()-high[i-28:i-14].max())
            low_change=np.sign(low[i-14:i].min()-low[i-28:i-14].min())
            structure=bool(high_change==side and low_change==side)
        assert direction[i]==side
        seen.add((run[i],side))
        output.append(dict(symbol=r.symbol,week=r.week,first_in_cloud=first,
                           cloud_crossings=crosses,few_cloud_traversals=crosses<=1 if crosses is not None else None,
                           directional_highs_lows=structure))
    return pd.DataFrame(output)

def outcome(frame,selected,known,yes):
    d=frame.loc[known,['symbol','side']].copy()
    d['keep']=selected.loc[d.index].astype(bool); d['yes']=yes.loc[d.index].astype(bool)
    cells=d.groupby(['symbol','side']).agg(n=('symbol','size'),k=('keep','sum'),success=('yes','sum'))
    return {'all':int(d.yes.sum()),'kept':int((d.yes&d.keep).sum()),'base_den':len(d),'kept_den':int(d.keep.sum()),
            'matched_expected':float((cells.k*cells.success/cells.n).sum())}

def evaluate(g,rule):
    keep=g[rule].astype('boolean').fillna(True)
    closed=g.exit_reason.isin(['stop','cloud_flip'])
    early_known=closed|g.observed_first_24h_bars
    duration=(pd.to_datetime(g.path_end_bar_bjt,utc=True)-pd.to_datetime(g.confirmed_bjt,utc=True)).dt.total_seconds()/3600
    early=g.exit_reason.eq('stop') & duration.ge(0)&duration.lt(24)
    closehit=g.confirmation_basis_lower_r.gt(10)
    closeknown=closehit|(closed&g.confirmation_basis_upper_r.le(10))
    return dict(signals=len(g),kept_signals=int(keep.sum()),feature_missing=int(g[rule].isna().sum()),
                chart=outcome(g,keep,g.status.isin(['hit','miss']),g.status.eq('hit')),
                close=outcome(g,keep,closeknown,closehit),early=outcome(g,keep,early_known,early),
                all_stops=outcome(g,keep,closed,g.exit_reason.eq('stop')))

def main():
    signals=pd.read_csv(SOURCE/'signals.csv',float_precision='round_trip')
    universe=signals[~signals.was_top20]
    chunks=[features(g,read_bars(SOURCE/'inputs'/s)) for s,g in universe.groupby('symbol')
            if g.confirmed_bjt.str[:4].isin(['2025','2026']).any()]
    ledger=universe.merge(pd.concat(chunks),on=['symbol','week'],validate='one_to_one')
    ledger['year']=ledger.confirmed_bjt.str[:4].astype(int)
    ledger=ledger[ledger.year.isin([2025,2026])]
    results={r:{str(y):evaluate(g,r) for y,g in ledger.groupby('year')} for r in RULES}
    assert ledger.groupby('year').size().to_dict()=={2025:2586,2026:3800}
    ledger[['symbol','week','confirmed_bjt','side',*RULES,'cloud_crossings','status','confirmation_basis_lower_r','exit_reason']].to_csv(EXP/'features.csv.gz',index=False)
    dump(EXP/'summary.json',results)
if __name__=='__main__':
    main()
