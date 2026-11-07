"""Exit early if previously untouched coins join the downside after entering the position.

At completed checkpoints use original 5m touch-candle closes up to that exact
instant. Reuse the corresponding native VectorBT market-exit fill at that open.
These exits share exactly the same six-day admission schedule as their control.
"""
from functools import lru_cache
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_selection as s
from yoyo.evaluation.monday_breadth_opt_match_exits import paired_month_p


@lru_cache(maxsize=1)
def touch_ledger():
    m=pd.read_csv(s.OUT/'features/members.csv.gz',usecols=['known','minutes','symbol'])
    m=m[m.minutes==5].copy()
    t=pd.to_datetime(m.known,utc=True)
    order=np.argsort(t.to_numpy())
    return pd.DatetimeIndex(t.iloc[order]).as_unit('ns').asi8,m.symbol.to_numpy()[order]


@lru_cache(maxsize=30000)
def expansion(w,x,known):
    times,symbols=touch_ledger()
    decision=known+pd.Timedelta(minutes=x)
    lo,hi=times.searchsorted([(known-pd.Timedelta(minutes=w)).value,decision.value],side='right')
    seen=set(symbols[lo:hi])
    amounts=[]
    for h in (6,12,24,48):
        last=times.searchsorted((decision+pd.Timedelta(hours=h)).value,side='right')
        amounts.append(len(set(symbols[hi:last])-seen))
    return amounts


def run():
    rules=pd.read_csv(study.OUT/'all_entries.csv')
    data=[];summary=[]
    for a in s.ASSETS:
        admitted=pd.read_parquet(study.OUT/'all_event_fills'/(a+'.parquet'))
        admitted=admitted[admitted.policy_id==5]
        for r in rules[rules.asset==a].itertuples():
            e=admitted[admitted.candidate_id==r.candidate_id].copy()
            if e.empty:continue
            fr,rets=s.inputs.cached_partition(r.minutes,r.wait_min)
            positions=pd.Index(fr.known).get_indexer(e.known)
            if (positions<0).any():raise ValueError('Missing known entry')
            ret=rets[positions,:,s.ASSETS.index(a)]
            source=ret[:,s.HOLDS.index(144)]
            # The natural comparison is market entry/exit with fee only, identical to a no-protection six-day trade.
            both=e.net_pct.notna().to_numpy()&np.isfinite(source)
            if not np.allclose(source[both],e.net_pct.to_numpy()[both],atol=1e-7):raise ValueError('Timed control differs from original six-day native fills')
            amounts=np.array([expansion(r.minutes,r.wait_min,k) for k in e.known])
            form=np.array([6,12,24,48])
            reach=e.initial.to_numpy()
            for checkpoints in ((6,),(12,),(24,),(6,12,24,48)):
                for rule,barrier in [('five',5),('ten',10),('twentyfive',25),('quarter_initial',.25*reach[:,None]),('one_initial',reach[:,None])]:
                    hit=(amounts>=barrier)&np.isin(form,checkpoints)[None,:]
                    exit_h=np.where(hit.any(axis=1),form[hit.argmax(axis=1)],144)
                    win=ret[np.arange(len(e)),np.searchsorted(s.HOLDS,exit_h)]
                    g=e[['epoch','decision','known','initial','new_coins']].copy()
                    g['net_pct']=win;g['baseline_pct']=source;g['difference']=win-source
                    g['exit_h']=exit_h;g['candidate_id']=r.candidate_id;g['asset']=a
                    g['checkpoints']='-'.join(map(str,checkpoints));g['threshold']=rule;data.append(g)
                    for ep,z in g.groupby('epoch'):
                        v=z.dropna(subset=['net_pct','baseline_pct'])
                        summary.append(dict(asset=a,candidate_id=r.candidate_id,checkpoints=g.checkpoints.iloc[0],threshold=rule,epoch=ep,
                              early=int((v.exit_h<144).sum()),base=v.baseline_pct.mean(),edge=v.difference.mean(),
                              paired_p=paired_month_p(pd.Series(v.difference.to_numpy(),index=v.decision)),**s.describe(v)))
    events=pd.concat(data,ignore_index=True);stats=pd.DataFrame(summary)
    events.to_parquet(study.OUT/'second_wave_exit_events.parquet',index=False)
    stats.to_csv(study.OUT/'second_wave_exit_folds.csv',index=False)
    grid=stats.pivot(index=['asset','candidate_id','checkpoints','threshold'],columns='epoch',values=['n','months','win','mean','lcb','dd','edge','early']).reset_index()
    grid.columns=[a if b=='' else a+str(b) for a,b in grid.columns]
    keep=(grid.n0>=12)&(grid.n1>=12)&(grid.n0+grid.n1>=30)&(grid.n2>=10)&(grid.months2>=4)&(grid.win2>=50)&(grid.dd2<=35)
    keep&=(grid.mean0>0)&(grid.mean1>0)&(grid.mean2>0)&(grid.n0*grid.win0+grid.n1*grid.win1>=50*(grid.n0+grid.n1))
    keep&=(grid.early0>=3)&(grid.early1>=3)&(grid.early2>=3)
    q=grid[keep].copy();q['floor']=q[['lcb0','lcb1','lcb2']].min(axis=1);q['edge_floor']=q[['edge0','edge1','edge2']].min(axis=1)
    q.sort_values(['edge_floor','floor'],ascending=False).groupby('asset').head(1).to_csv(study.OUT/'second_wave_chosen.csv',index=False)


if __name__=='__main__':run()
