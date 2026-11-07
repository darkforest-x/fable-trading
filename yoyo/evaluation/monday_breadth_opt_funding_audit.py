"""Apply actual funding settlements to frozen 2026 winners; preserve incomplete basket coverage."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_funding as fund
from yoyo.evaluation import monday_breadth_opt_exits as sim
from yoyo.evaluation.monday_breadth_opt_payoffs import load_full_ohlc


def tasks():
    all_events=[]
    for where,roles in [(study.OUT,('dynamic_primary','retention','case_target','case_runner')),
                        (study.search.SEARCH,('time_exit_winner',))]:
        e=pd.read_parquet(where/'locked_events.parquet')
        rule=pd.read_csv(where/'locked_rules.csv')
        rule=rule[rule.role.isin(roles)]
        cols=['candidate_id','minutes','hold_hours','role']
        if where==study.OUT:cols+=['policy_id']
        e=e[(e.epoch==3)&e.net_pct.notna()].drop(columns=['minutes','hours','policy_id'],errors='ignore').merge(
            rule[cols],on='candidate_id',validate='many_to_one')
        if where!=study.OUT:e['policy_id']=-1
        all_events.append(e)
    events=pd.concat(all_events,ignore_index=True)
    events['row_id']=np.arange(len(events))
    alt=events[events.asset.eq('ALT')].merge(study.member_bridge(),on=['minutes','known'])
    other=events[~events.asset.eq('ALT')].assign(symbol=lambda z:z.asset+'USDT')
    return events,pd.concat([alt,other],ignore_index=True)


def per_symbol(task):
    sym,g=task;g=g.copy()
    timed=g.policy_id.eq(-1)
    sub=g[timed].copy()
    if len(sub):
        f,_=load_full_ohlc(sym,sub.decision.min(),sub.decision.max(),needed_intervals=None,repair_missing=False)
        sub['entry_open']=f.open.reindex(sub.decision).to_numpy()
        sub['exit_time']=sub.decision+pd.to_timedelta(sub.hold_hours,unit='h')
        sub['first_exit_time']=sub.exit_time;sub['exit_legs']=1
    dy=g[(~timed)&(g.policy_id<1000)].copy()
    if len(dy):
        f=pd.read_parquet(study.CACHE/(sym+'.parquet'),columns=['entry_time','policy_id','entry_open','first_exit_time','exit_time','exit_legs'])
        dy=dy.merge(f,left_on=['decision','policy_id'],right_on=['entry_time','policy_id'],validate='many_to_one')
    adv=g[g.policy_id>=1000].copy()
    if len(adv):
        f=pd.read_parquet(fund.FUNDED_CACHE,columns=['entry_time','policy_id','entry_open','first_exit_time','exit_time','exit_legs','net_pct'])
        f['policy_id']+=1000
        adv=adv.drop(columns='net_pct').merge(f,left_on=['decision','policy_id'],right_on=['entry_time','policy_id'],validate='many_to_one')
    out=pd.concat([sub,dy,adv],ignore_index=True)
    out['entry_time']=out.decision
    # Each trade spans at most two calendar months. Do not let a missing late-month archive remove earlier dates.
    out['months']=list(zip(out.decision.dt.strftime('%Y-%m'),out.exit_time.dt.strftime('%Y-%m')))
    policy={i:p for i,p in enumerate(sim.policies(sym=='BTCUSDT'))}
    policy[-1]=SimpleNamespace(take_fraction=0)
    policy.update({1000+i:p for i,p in enumerate(fund.funded_policies())})
    parts=[]
    for months,s in out.groupby('months'):
        f=fund.rates(sym,months=tuple(sorted(set(months))))
        charge=fund.charged(s,f,policy)
        s=s.copy();s['funding_pct']=100*charge/s.entry_open
        proxied=f.index[f.price_proxy] if not f.empty else pd.DatetimeIndex([],tz='UTC')
        s['mark_proxy']=(proxied.searchsorted(s.exit_time,side='right')>proxied.searchsorted(s.entry_time,side='left'))&s.funding_pct.notna()
        s['already_charged']=s.policy_id>=1000
        parts.append(s[['row_id','symbol','funding_pct','mark_proxy','already_charged']])
    return pd.concat(parts,ignore_index=True)


def run():
    events,bridge=tasks()
    with ThreadPoolExecutor(3) as pool:
        by=pd.concat(pool.map(per_symbol,bridge.groupby('symbol')),ignore_index=True)
    x=by.groupby('row_id').agg(funding_pct=('funding_pct','mean'),priced=('funding_pct','count'),
        proxy_marks=('mark_proxy','sum'),already_charged=('already_charged','max'))
    x['expected']=bridge.groupby('row_id').size()
    x=x.reindex(events.row_id)
    events['funding_pct']=x.funding_pct.to_numpy()
    events['complete_funding']=x.priced.eq(x.expected).to_numpy()
    events['proxy_members']=x.proxy_marks.to_numpy()
    events['funded_pct']=(events.net_pct-np.where(x.already_charged,0,events.funding_pct)).where(events.complete_funding)
    events.to_parquet(study.OUT/'finalists_funding_events_2026.parquet',index=False)
    s=events.groupby('candidate_id').agg(scheduled=('row_id','size'),evaluated=('funded_pct','count'),
        before_or_already_funded=('net_pct','mean'),after_funding=('funded_pct','mean'),
        win_pct=('funded_pct',lambda x:100*(x.dropna()>0).mean()),
        avg_funding=('funding_pct','mean'),events_with_mark_proxy=('proxy_members',lambda x:x.gt(0).sum())).reset_index()
    s.to_csv(study.OUT/'finalists_funding_2026.csv',index=False)


if __name__=='__main__':run()
