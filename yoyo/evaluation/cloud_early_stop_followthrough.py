"""What happened AFTER the frozen original fixed stop? Descriptive future labels only.
Cohort is first-day stops in the two independent calendar slices. Starting at
the CLOSE of the stop's own 12H candle, observe the next 60 consecutive 12H
bars. Keep the original 1.618 price, original 3% risk and original direction.
Tabulate whether the original 10R target is strictly exceeded at any time,
also whether it was reached before the original EMA20/50 intrabar invalidation.
Do not count the stop candle's favorable wick in any result.
"""
import numpy as np
import pandas as pd
from yoyo.evaluation.cloud_pretouch_structure import EXP, SOURCE, STEP, read_bars, ema_points, dump

A20,A50=2/21,2/51

def cloud_crossed(prices,fast,slow,side):
    e20=fast+(prices-fast)*A20
    e50=slow+(prices-slow)*A50
    eps=8*np.finfo(float).eps*np.maximum(1,np.maximum(abs(e20),abs(e50)))
    return side*(e20-e50)<-eps

def after_stop(r,b,ema):
    ms=int(pd.Timestamp(r.path_end_bar_bjt).timestamp()*1000)
    i=b.index.get_loc(ms)
    risk=abs(r.reference_entry-r.reference_stop)
    target=r.reference_entry+r.side*10*risk
    adjacent=b.iloc[i:i+61]
    steps=(adjacent.index.to_numpy()-ms)//STEP
    gaps=np.flatnonzero(steps!=np.arange(len(steps)))
    if len(gaps): adjacent=adjacent.iloc[:gaps[0]]
    last=len(adjacent)-1
    future=adjacent.iloc[1:]
    favorable='high' if r.side==1 else 'low'
    adverse='low' if r.side==1 else 'high'
    over=(future[favorable]-target)*r.side>0
    original_entry_back=((future.iloc[:2][favorable]-r.reference_entry)*r.side>=0).any()
    prior=ema.iloc[i-1:i+last]
    broken=cloud_crossed(adjacent[adverse].to_numpy(),prior.ema20.to_numpy(),prior.ema50.to_numpy(),r.side)
    flips=np.flatnonzero(broken)
    turnover=int(flips[0]) if len(flips) else None
    if turnover==0:
        cloud='miss'
    elif turnover is None:
        cloud='hit' if over.any() else 'miss' if last==60 else 'censored'
    elif over.iloc[:turnover-1].any():
        cloud='hit'
    else:
        bar=adjacent.iloc[turnover]
        if (bar.open-target)*r.side>0:
            cloud='hit'
        elif over.iloc[turnover-1] and not cloud_crossed(bar.open,prior.ema20.iloc[turnover],prior.ema50.iloc[turnover],r.side):
            cloud='possible'
        else: cloud='miss'
    close_r=(adjacent.close.iloc[0]-r.reference_entry)*r.side/risk
    adverse_r=(adjacent[adverse].iloc[0]-r.reference_entry)*r.side/risk
    return dict(symbol=r.symbol,week=r.week,year=int(r.confirmed_bjt[:4]),side=r.side,
                stop_bar_close_back=bool((adjacent.close.iloc[0]-r.reference_stop)*r.side>0),
                stop_bar_close_r=float(close_r),stop_bar_adverse_r=float(adverse_r),
                stop_bar_open=r.path_end_bar_bjt,observed_12h=last,cloud_flip_same_stop_candle=turnover==0,
                original_line_back_next_two='yes' if original_entry_back else 'no' if last>=2 else 'censored',
                above_original_10r='hit' if over.any() else 'miss' if last==60 else 'censored',
                above_10r_before_cloud_flip=cloud)

def main():
    vals=pd.read_csv(SOURCE/'signals.csv',float_precision='round_trip')
    days=(pd.to_datetime(vals.path_end_bar_bjt,utc=True)-pd.to_datetime(vals.confirmed_bjt,utc=True)).dt.total_seconds()/3600
    early=vals.loc[~vals.was_top20 & vals.confirmed_bjt.str[:4].isin(['2025','2026']) &
                   vals.exit_reason.eq('stop') & days.ge(0) & days.lt(24)]
    rows=[]
    for sym,g in early.groupby('symbol'):
        b=read_bars(SOURCE/'inputs'/sym)
        ema=pd.DataFrame(ema_points(b[['close']].reset_index().to_dict('records')))
        rows.extend(after_stop(r,b,ema) for r in g.itertuples())
    out=pd.DataFrame(rows)
    assert out.year.value_counts().to_dict()=={2025:834,2026:1007}
    out.to_csv(EXP/'early_stop_paths.csv.gz',index=False)
    counts={str(y):{k:g[k].value_counts().to_dict() for k in ['above_original_10r','above_10r_before_cloud_flip','original_line_back_next_two']} for y,g in out.groupby('year')}
    for year,g in out.groupby('year'):
        subsets={'all':g,'cloud_still_valid_at_stop_bar_end':g[~g.cloud_flip_same_stop_candle]}
        for label,subset in subsets.items():
            counts[str(year)][label+'_by_close_back']={
                str(bool(back)):{'n':len(rows),'trend_10r':rows.above_10r_before_cloud_flip.value_counts().to_dict(),
                'close_r_q05_median_q95':rows.stop_bar_close_r.quantile([.05,.5,.95]).tolist(),
                'worst_intrabar_r':float(rows.stop_bar_adverse_r.min())}
                for back,rows in subset.groupby('stop_bar_close_back')}
    dump(EXP/'early_stop_paths.json',counts)
if __name__=='__main__':
    main()
