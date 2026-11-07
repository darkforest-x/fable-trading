"""Mark already-filled finalist trades on a common 5m clock, including synchronized basket peaks.

Execution is read from the original VectorBT fill ledger. Each event starts with
one notional shared equally among its original constituents, including entry
fee, without mixing calendar times of their separate highest prices.
"""
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exits as sim
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation.monday_breadth_opt_payoffs import load_full_ohlc


def one_symbol(task):
    sym,group=task
    windows=[(r.decision,r.decision+pd.Timedelta(hours=r.hours)) for r in group.itertuples()]
    raw,_=load_full_ohlc(sym,min(t for t,_ in windows),max(t for _,t in windows),needed_intervals=windows,repair_missing=False)
    if group.policy_id.ge(1000).any():
        from yoyo.evaluation import monday_breadth_opt_funding as funding
        adv=pd.read_parquet(funding.FUNDED_CACHE)
        adv['policy_id']+=1000
        financing=funding.rates('BTCUSDT')
        funding_policies=funding.funded_policies()
    fills=pd.read_parquet(study.CACHE/(sym+'.parquet')).set_index(['entry_time','policy_id'])
    policies=sim.policies(sym=='BTCUSDT')
    for r in group.itertuples():
        is_funded=r.policy_id>=1000
        source=adv.set_index(['entry_time','policy_id']) if is_funded else fills
        if (r.decision,r.policy_id) not in source.index:continue
        fill=source.loc[(r.decision,r.policy_id),:]
        pol=funding_policies[r.policy_id-1000] if is_funded else policies[r.policy_id]
        n=r.hours*12
        index=pd.date_range(r.decision,periods=n+1,freq='5min')
        path=raw.reindex(index)
        if path.close.iloc[:-1].isna().any() or not np.isfinite(path.open.iloc[-1]):continue
        mark=np.r_[path.close.iloc[:-1].to_numpy(),path.open.iloc[-1]]/fill.entry_open-1
        marked=mark-sim.TAKER
        first=int((fill.first_exit_time-r.decision)/pd.Timedelta(minutes=5))
        last=int((fill.exit_time-r.decision)/pd.Timedelta(minutes=5))
        if fill.exit_legs==2:
            # The first leg can only be the resting, size-limited take-profit order.
            first_price=fill.first_exit_price if is_funded else fill.entry_open*(1+pol.take_pct/100)
            realized=pol.take_fraction*(first_price/fill.entry_open*(1-sim.MAKER)-1)
            marked[first:last]=realized+(1-pol.take_fraction)*mark[first:last]-sim.TAKER
        marked[last:]=fill.net_pct/100
        if is_funded:
            raw_cf=(financing.realized_rate*financing.mark_price).reindex(index,fill_value=0.).to_numpy()
            qty=np.ones(n+1);qty[last+1:]=0.
            if fill.exit_legs==2:
                qty[first+1:last+1]=1-pol.take_fraction
                if raw_cf[first]<0:qty[first]=1-pol.take_fraction
            if raw_cf[0]<0:qty[0]=0.
            if raw_cf[last]<0:qty[last]=0.
            marked-=np.cumsum(qty*raw_cf)/fill.entry_open
            marked[last:]=fill.funded_pct/100
        yield r.row_i,np.r_[0,marked],np.r_[0,mark]


def run():
    out=study.OUT
    rules=pd.read_csv(out/'locked_rules.csv')
    events=pd.read_parquet(out/'locked_events.parquet')
    events=events[(events.epoch==3)&events.net_pct.notna()].merge(rules[['candidate_id','minutes']].rename(columns={'minutes':'w'}),on='candidate_id')
    events=events.sort_values(['candidate_id','decision']).reset_index(drop=True)
    events['row_i']=np.arange(len(events))
    basket=study.member_bridge().rename(columns={'minutes':'w'})
    alt=events[events.asset=='ALT'].merge(basket,on=['w','known'],how='inner')
    other=events[events.asset!='ALT'].assign(symbol=lambda f:f.asset+'USDT')
    jobs=pd.concat([alt,other]).groupby('symbol',sort=True)
    m=[np.zeros(r.hours*12+2) for r in events.itertuples()]
    q=[np.zeros_like(a) for a in m]; counts=np.zeros(len(events),int)
    def collect(task):return list(one_symbol(task))
    with ThreadPoolExecutor(2) as pool:
        for results in pool.map(collect,jobs):
            for i,marked,unmanaged in results:
                counts[i]+=1;m[i]+=marked;q[i]+=unmanaged
    result=[]; summary=[]
    for key,s in events.groupby('candidate_id'):
        chunks=[];peak=1.;equity=1.;worst=0.
        for r in s.itertuples():
            i=r.row_i
            if counts[i]!=r.expected:continue
            marked=m[i]/counts[i]; gross=q[i]/counts[i]
            if not np.isclose(marked[-1]*100,r.net_pct,atol=1e-7):raise ValueError('Portfolio versus original net return drift')
            p=equity*(1+marked)
            peaks=np.maximum.accumulate(np.maximum(p,peak))
            event_dd=100*np.max(1-p/peaks)
            worst=max(worst,event_dd);peak=peaks[-1];equity=p[-1]
            chunks.append(i)
            result.append(dict(candidate_id=key,decision=r.decision,n=counts[i],net_pct=r.net_pct,
                worst_mark_from_entry_pct=100*marked.min(),best_mark_from_entry_pct=100*marked.max(),
                synchronous_unmanaged_close_peak_pct=100*gross.max(),average_member_high_peak_pct=r.peak_pct,
                drawdown_from_prior_or_current_peak_pct=event_dd))
        summary.append(dict(candidate_id=key,priced_events=len(chunks),expected_events=len(s),marked_close_drawdown_pct=worst,
                  compounded_full_notional_return_pct=100*(equity-1)))
    pd.DataFrame(result).to_csv(out/'marked_risk_events_2026.csv',index=False)
    pd.DataFrame(summary).to_csv(out/'marked_risk_summary_2026.csv',index=False)


if __name__=='__main__':
    run()
