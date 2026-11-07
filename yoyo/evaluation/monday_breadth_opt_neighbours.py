"""Inspect one-parameter neighbours of frozen winners, without using the inspection to promote another."""
from dataclasses import asdict
from types import SimpleNamespace
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_selection as s
from yoyo.evaluation import monday_breadth_opt_exits as sim
from yoyo.evaluation import monday_breadth_opt_exit_study as study


def adjacent(grid,value):
    i=grid.index(value)
    return grid[max(0,i-1):i]+grid[i+1:i+2]


def timed():
    winners=pd.read_csv(s.SEARCH/'locked_rules.csv')
    results=[]
    for r in winners[winners.role.eq('time_exit_winner')].itertuples():
        co=r.confirmation
        if co.startswith('btc_move_'):confirm=['btc_move_negative','btc_move_0to1','btc_move_ge1']
        elif co.startswith('alt_move_'):confirm=['alt_move_negative','alt_move_0to2','alt_move_ge2']
        elif co.startswith('reclaim_'):confirm=['reclaim_lt25','reclaim_25to75','reclaim_ge50','reclaim_ge75']
        else:confirm=['none','fresh_cluster','prior_shock_6h','no_prior_shock_24h']
        breadths=[]
        if r.breadth.startswith('share_'):breadths=[f'share_{int(v)}' for v in s.CFG['breadth_share_min_pct']]
        else:
            a,b=map(int,r.breadth[1:].split('_'))
            comparable=[(name,lo,hi) for name,lo,hi in s.RANGES if (hi==100000)==(b==100000)]
            by_distance=lambda x:abs(np.log(x[1]/a))+abs(np.log(x[2]/b))
            breadths=[v[0] for v in sorted(comparable,key=by_distance)[1:3]]
        axes={'minutes':adjacent(s.CFG['W_minutes'],r.minutes),'wait_min':adjacent(s.CFG['X_minutes'],r.wait_min),
              'hold_hours':adjacent(list(s.HOLDS),r.hold_hours),'breadth':breadths,'confirmation':['none',*confirm],
              'diffusion':['any','zero','at_most_four','ratio_le_25pct']}
        alternatives=[('selected',None)]
        alternatives.extend((col,v) for col,choices in axes.items() for v in dict.fromkeys(choices) if v!=getattr(r,col))
        for field,v in alternatives:
            a=r._asdict()
            if field!='selected':a[field]=v
            cand=SimpleNamespace(**a)
            f,ret=s.inputs.cached_partition(cand.minutes,cand.wait_min)
            _,d,c=s.rules(f,cand.minutes,cand.wait_min)
            if cand.diffusion not in d or cand.confirmation not in c:continue
            z=s.evaluate_rule(cand,f,ret)
            for epoch,group in z.groupby('epoch'):
                results.append(dict(candidate_id=r.candidate_id,asset=r.asset,changed=field,value=v,epoch=epoch,**s.describe(group)))
    pd.DataFrame(results).to_csv(s.SEARCH/'one_parameter_neighbours.csv',index=False)


def exits():
    ls=pd.read_csv(study.OUT/'locked_rules.csv')
    records=[]
    for asset,group in ls[ls.role.isin(['dynamic_primary','retention'])].groupby('asset'):
        policies=sim.policies(asset=='BTC')
        f=pd.read_parquet(study.OUT/'all_event_fills'/(asset+'.parquet'))
        for r in group.itertuples():
            selected=asdict(policies[r.policy_id])
            entry=f[f.candidate_id==r.entry_id]
            for i,p in enumerate(policies):
                variant=asdict(p)
                diff=[k for k,v in selected.items() if v!=variant[k]]
                if len(diff)>1:continue
                changed=diff[0] if diff else 'selected'
                for epoch,z in entry[entry.policy_id==i].groupby('epoch'):
                    records.append(dict(candidate_id=r.candidate_id,asset=asset,changed=changed,
                        value=variant.get(changed),epoch=epoch,policy_id=i,key=p.key,**s.describe(z)))
    pd.DataFrame(records).to_csv(study.OUT/'one_parameter_exit_neighbours.csv',index=False)


if __name__=='__main__':
    import sys
    {'timed':timed,'exits':exits}[sys.argv[1]]()
