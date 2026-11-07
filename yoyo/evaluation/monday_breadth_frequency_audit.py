"""Audit frequency and one-account opportunity counts on frozen native VectorBT trades.

Each asset rule keeps its own alert clock; the shared account accepts the earliest
available signal. For the financial audit, original fills determine PnL. Five
minute closes value positions prior to their exit, never determining admissions.
Historical settlement rates are used only to account for costs after entry.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pandas as pd

from yoyo.data.altseason_funding import FundingClient, collect
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_funding as fund
from yoyo.evaluation import monday_breadth_opt_payoffs as quotes
from yoyo.evaluation.monday_breadth_frequency_long import EXP, CACHE, CUTOFF
from yoyo.evaluation.monday_breadth_opt_selection import describe

PRIORITY = {'BTC':0,'ETH':1,'SOL':2,'ALT':3}
FUND_DIR=CACHE.parent/'funding_2026'


def frozen(family):
    directory=EXP/family
    r=pd.read_csv(directory/'locked_rules.csv')
    e=pd.read_parquet(directory/'locked_events.parquet')
    if len(r.candidate_id.unique())!=len(r):
        raise ValueError('Candidate ID is not unique')
    for k in ('minutes','wait_min','hold_hours','asset'):
        e=e.drop(columns=k,errors='ignore')
    e=e.merge(r[['candidate_id','asset','minutes','wait_min','hold_hours']],on='candidate_id',validate='many_to_one')
    if family=='fixed':
        e['exit_time']=e.decision+pd.to_timedelta(e.hold_hours,unit='h')
    e['can_enter']=True
    for (w,x,h),g in e[e.net_pct.isna()].groupby(['minutes','wait_min','hold_hours']):
        if not g.asset.eq('SOL').all():raise ValueError('Unexpected incomplete native outcome')
        source=study.search.OUT/'payoffs'/f'event_outcomes_minutes{w}_wait{x}.csv.gz'
        bars=pd.read_csv(source,usecols=['known','hold_hours','sol_entry_open'],parse_dates=['known'])
        known_opens=bars[bars.hold_hours.eq(h)].set_index('known').sol_entry_open.reindex(g.known)
        if known_opens.notna().any():raise ValueError('An admitted trade lacks its exit')
        e.loc[g.index,'can_enter']=False
    e['source_id']=e.candidate_id
    return r,e


def shared_account(rules,events):
    all_events=[]; flow=[]
    target_col=next((v for v in ('frequency_target','frequency_tier','target_per_year','tier','tier_target') if v in rules.columns),None)
    if target_col is None:raise ValueError(f'Unrecognized frequency tier: {rules.columns.tolist()}')
    for target,rs in rules.groupby(target_col):
        eligible=rs
        if 'frequency_pass' in rs.columns:eligible=rs[rs.frequency_pass]
        base=events[events.candidate_id.isin(eligible.candidate_id)].copy()
        base['priority']=base.asset.map(PRIORITY)
        base=base.sort_values(['decision','priority','candidate_id'],kind='stable')
        picked=[]
        after=pd.Timestamp.min.tz_localize('UTC')
        for row in base.itertuples():
            take=row.can_enter and row.decision>=after
            flow.append(dict(target=target,epoch=row.epoch,decision=row.decision,asset=row.asset,source_id=row.candidate_id,taken=take))
            if take:
                picked.append(row.Index)
                after=max(row.exit_time,row.decision+pd.Timedelta(hours=24))
        keep=base.loc[picked].copy()
        keep['candidate_id']=f'account_{int(target)}'
        all_events.append(keep)
    return pd.concat(all_events,ignore_index=True),pd.DataFrame(flow)


def cash_after_funding(group,history):
    fills=group.assign(entry_time=group.decision,policy_id=0,exit_legs=1)
    fills['first_exit_time']=fills.exit_time
    return 100*fund.charged(fills,history,{0:SimpleNamespace(take_fraction=0)})/fills.entry_open.to_numpy()


def funded_instrument(sym,group,client):
    out=[]
    exact=FUND_DIR/'normalized/binance'/(sym+'_funding.csv.gz')
    for months,g in group.groupby('months'):
        raw=fund.exact_rates(exact) if exact.is_file() else fund.rates(sym,months=tuple(sorted(set(months))))
        values=cash_after_funding(g,raw)
        g=g.copy()
        g['funding_pct']=values
        proxies=raw.index[raw.price_proxy] if not raw.empty else pd.DatetimeIndex([],tz='UTC')
        g['mark_proxy']=proxies.searchsorted(g.exit_time,side='right')>proxies.searchsorted(g.decision,side='left')
        out.append(g)
    a=pd.concat(out)
    if a.funding_pct.isna().any() and not exact.is_file():
        try:
            start=pd.Timestamp('2026-01-01',tz='UTC').value//10**6
            end=fund.SOURCE_END.value//10**6
            collect(client,sym,start,end)
            h=fund.exact_rates(exact)
            missing=a.funding_pct.isna()
            a.loc[missing,'funding_pct']=cash_after_funding(a[missing],h)
            a.loc[missing,'mark_proxy']=False
        except (OSError,ValueError,RuntimeError) as exc:
            print(f'Incomplete funding {sym}: {exc}',flush=True)
    return a


def symbol_audit(pair,client):
    symbol,g=pair
    f,_=quotes.load_full_ohlc(symbol,g.decision.min(),g.exit_time.max(),repair_missing=False)
    g=g.copy()
    opens=f.open.reindex(g.decision).to_numpy()
    g['entry_open']=g.get('entry_open',pd.Series(index=g.index,dtype=float)).fillna(pd.Series(opens,index=g.index))
    g['months']=list(zip(g.decision.dt.strftime('%Y-%m'),g.exit_time.dt.strftime('%Y-%m')))
    if g.entry_open.isna().any():raise ValueError(f'Native entry price unavailable for {symbol}')
    g=funded_instrument(symbol,g,client)
    paths=[]
    for r in g.itertuples():
        bars=pd.date_range(r.decision,r.exit_time,freq='5min',inclusive='left') if r.decision<r.exit_time else []
        closes=f.close.reindex(bars).to_numpy()
        exit_open=f.open.get(r.exit_time,np.nan)
        # An endpoint reconciliation and a marked-to-market path. Source returns remain native.
        final=r.net_pct if r.family=='long' else 100*((exit_open/r.entry_open)*(1-.0005)-1-.0005)
        path=np.r_[0.,-.0005,closes/r.entry_open-1-.0005,final/100]
        if not np.isfinite(path).all():raise ValueError(f'Incomplete 5-minute marking path: {symbol} {r.decision}')
        paths.append((r.event_key,path))
    return g[['event_key','symbol','funding_pct','mark_proxy']],paths


def audit(source,folder):
    e=source[source.epoch.eq(3)&source.net_pct.notna()].copy().reset_index(drop=True)
    e['event_key']=np.arange(len(e))
    alt=e[e.asset.eq('ALT')].merge(study.member_bridge(),on=['minutes','known'])
    direct=e[~e.asset.eq('ALT')].assign(symbol=lambda x:x.asset+'USDT')
    bridge=pd.concat([direct,alt],ignore_index=True)
    if bridge.duplicated(['event_key','symbol']).any():raise ValueError('Duplicate member')
    client=FundingClient('binance',FUND_DIR)
    n=bridge.groupby('event_key').size()
    parts=[]; marks={}
    with ThreadPoolExecutor(6) as pool:
        jobs=list(bridge.groupby('symbol',sort=False))
        for j,(funds,paths) in enumerate(pool.map(lambda q:symbol_audit(q,client),jobs),1):
            parts.append(funds)
            for k,path in paths:
                if k not in marks:marks[k]=np.zeros_like(path)
                marks[k]+=path/n[k]
            if j%100==0:print('priced',j,'/',len(jobs),flush=True)
    funding=pd.concat(parts,ignore_index=True)
    funding.to_parquet(folder/'member_funding.parquet',index=False)
    by=funding.groupby('event_key').agg(members=('symbol','size'),observed=('funding_pct','count'),funding_pct=('funding_pct','mean'),proxy_count=('mark_proxy','sum'))
    e=e.join(by,on='event_key')
    e['funded_pct']=(e.net_pct-e.funding_pct).where(e.observed.eq(e.members))
    # A scalar path summary is sufficient to combine arbitrarily selected disjoint trades.
    markstats=[]
    for idx in e.event_key:
        path=marks[idx]; end=e.loc[idx,'net_pct']
        if not np.isclose(100*path[-1],end,atol=1e-7):raise ValueError(f'Native net endpoint mismatch {idx}')
        p=1+path
        markstats.append((p.max(),p.min(),float((1-p/np.maximum.accumulate(p)).max())))
    e[['mark_high','mark_low','inside_dd']]=markstats
    e.to_parquet(folder/'costed_events.parquet',index=False)
    return e


def stats(group):
    g=group.sort_values('decision')
    e=describe(g)
    if not len(g):return e
    gain=g.net_pct
    e.update(first=g.decision.min(),last=g.decision.max(),largest_gap_days=g.decision.diff().dt.total_seconds().max()/86400,
             profit_factor=gain[gain>0].sum()/-gain[gain<0].sum() if (gain<0).any() else np.nan)
    if 'funded_pct' in g and g.funded_pct.notna().any():
        v=g.dropna(subset=['funded_pct']).copy()
        e['funded_n']=len(v);e['funded_mean']=v.funded_pct.mean();e['funded_win']=100*v.funded_pct.gt(0).mean()
        ef=describe(v.assign(net_pct=v.funded_pct))
        e.update(funded_settled_dd=ef['dd'],funded_ex_best_month=ef['exmonth'],funded_stress=ef['stress'])
        equity=peak=1.;worst=0.
        for r in g.itertuples():
            worst=max(worst,r.inside_dd,1-equity*r.mark_low/peak)
            peak=max(peak,equity*r.mark_high)
            equity*=1+r.net_pct/100
        e['marked_close_dd_before_funding']=worst*100
    return e


def run(family,reuse_costs=False):
    folder=EXP/'audit'/family;folder.mkdir(parents=True,exist_ok=True)
    rules,events=frozen(family)
    events['family']=family
    basket,flow=shared_account(rules,events) if family=='fixed' else (pd.DataFrame(),pd.DataFrame())
    if len(flow):flow.to_parquet(folder/'account_alerts.parquet',index=False)
    if reuse_costs:
        supplement=pd.read_parquet(folder/'costed_events.parquet')
        fields=['source_id','decision','exit_time','net_pct','asset','known']
        expected=events[events.epoch.eq(3)&events.net_pct.notna()]
        def native_keys(frame):
            return frame[fields].sort_values(['source_id','decision']).reset_index(drop=True)
        pd.testing.assert_frame_equal(native_keys(expected),native_keys(supplement))
    else:
        supplement=audit(events,folder)
    cols=['source_id','decision','funded_pct','mark_high','mark_low','inside_dd']
    combined=pd.concat([events,basket],ignore_index=True)
    combined=combined.merge(supplement[cols],on=['source_id','decision'],how='left',validate='many_to_one')
    combined.to_parquet(folder/'all_rule_and_account_events.parquet',index=False)
    rows=[];monthly=[]
    for cid,g in combined.groupby('candidate_id'):
        for label,sub in [('all',g),*[(str(k),v) for k,v in g.groupby(g.decision.dt.year)]]:
            if label!='2026':sub=sub.drop(columns=['funded_pct'])
            rows.append(dict(candidate_id=cid,period=label,**stats(sub)))
        for m in pd.period_range('2026-01','2026-10',freq='M'):
            sub=g[g.decision.dt.strftime('%Y-%m').eq(str(m))]
            monthly.append(dict(candidate_id=cid,month=str(m),n=len(sub),funded_n=sub.funded_pct.count(),net=sub.net_pct.mean(),funded=sub.funded_pct.mean()))
    pd.DataFrame(rows).to_csv(folder/'performance.csv',index=False)
    pd.DataFrame(monthly).to_csv(folder/'frequency_monthly_2026.csv',index=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('family',choices=['long','fixed'])
    parser.add_argument('--reuse-costs',action='store_true',help='Reuse verified asset fills and funding when only account admission changes')
    args=parser.parse_args()
    run(args.family,args.reuse_costs)
