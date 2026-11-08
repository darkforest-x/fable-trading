"""Prespecified failure clustering check and unique trend check, independent of rule selection.
At confirmation consult prior same-symbol same-side stops in the first two complete
12H bars, known at the stop bar close. Suppress a new alert for 14 elapsed days.
No P&L or positions. Independent same-peak proxy = symbol/side/peak 12H open.
"""
import pandas as pd
import numpy as np
from yoyo.evaluation.cloud_allmarket_census import EXP
from yoyo.evaluation.cloud_peak10_inputs import dump

START = pd.Timestamp('2025-01-01',tz='Asia/Shanghai')
def stop_cooldown(g):
    prior = g.loc[g.early,'outcome_known'].dropna().sort_values().astype('int64').to_numpy()
    t = g.confirm.astype('int64').to_numpy()
    i = np.searchsorted(prior,t,side='right')-1
    keep = np.ones(len(t),dtype=bool)
    if len(prior):
        keep[i>=0] = t[i>=0]-prior[i[i>=0]] >= pd.Timedelta(days=14).value
    return pd.Series(keep,index=g.index)

def rates(x, keep):
    x=x.copy(); x['k']=keep.reindex(x.index)
    resolved=x.status.isin(['hit','miss'])
    true_close=x.exit_reason.isin(['stop','cloud_flip'])
    results=dict(signals=len(x),retained=int(x.k.sum()),known_10r=int(resolved.sum()),known_exit=int(true_close.sum()))
    for title,v,d in [('hits',x.status.eq('hit'),resolved),('confirmed_price_hits',x.confirmation_basis_lower_r.gt(10),x.confirm_status_known),
                      ('early_stops',x.early,x.early_observed),('all_stops',x.exit_reason.eq('stop'),true_close)]:
        b=x.loc[d,['symbol','side','k']].copy(); b['yes']=v[d]
        blocks=b.groupby(['symbol','side']).agg(n=('yes','size'),k=('k','sum'),yes=('yes','sum'))
        results[title]=dict(all=int(b.yes.sum()),kept=int((b.yes & b.k).sum()),kept_known=int(b.k.sum()),
            all_known=len(b),equal_count_random_expected=float((blocks.k*blocks.yes/blocks.n).sum()))
    h=x[x.status=='hit']
    cols=['symbol','side','peak_time_bjt']
    results['distinct_peak_bars']=dict(all=len(h.drop_duplicates(cols)),kept=len(h[h.k].drop_duplicates(cols)))
    results['peak_then_fixed_stop']=int((h.exit_reason=='stop').sum())
    return results

def main():
    a=pd.read_csv(EXP/'signals.csv')
    s=pd.read_csv(EXP/'冻结候选入选明细.csv',usecols=['symbol','week','confirmed_bjt'])
    a['primary_rule_kept']=pd.MultiIndex.from_frame(a[s.columns]).isin(pd.MultiIndex.from_frame(s))
    a['confirm']=pd.to_datetime(a.confirmed_bjt,utc=True).dt.tz_convert('Asia/Shanghai')
    ex=pd.to_datetime(a.path_end_bar_bjt,utc=True,format='mixed').dt.tz_convert('Asia/Shanghai')
    a['outcome_known']=ex+pd.Timedelta(hours=12)
    a['early']=a.exit_reason.eq('stop') & ex.ge(a.confirm) & ex.lt(a.confirm+pd.Timedelta(hours=24))
    a['early_observed']=a.exit_reason.isin(['stop','cloud_flip']) | (a.exit_reason.eq('open') &
       (a.confirm + pd.Timedelta(hours=24) <= pd.Timestamp('2026-11-08T12:00:00Z')))
    a['confirm_status_known']=a.confirmation_basis_lower_r.gt(10) | (a.exit_reason.isin(['stop','cloud_flip']) & a.confirmation_basis_upper_r.le(10))
    a['cooldown_kept']=True
    for _,g in a.groupby(['symbol','side','pre_current_listing']):
        a.loc[g.index,'cooldown_kept']=stop_cooldown(g)
    x=a[~a.was_top20 & (a.confirm >= START)]
    results={rule:{str(year):rates(y,y[rule]) for year,y in x.groupby(x.confirm.dt.year)}
             for rule in ('primary_rule_kept','cooldown_kept')}
    dump(EXP/'cooldown_and_distinct_trends.json',results)

if __name__=='__main__':
    main()
