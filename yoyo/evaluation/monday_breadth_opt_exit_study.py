"""Fit and audit adaptive exit policies on frozen entry candidates and named anchors.

At each maximum holding horizon, accept the first eligible entry and wait through
its planned window even if protection exits early. This makes exit policies share
the same entry set. Each basket has equal notionals across its initial altcoins.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_exits as sim
from yoyo.evaluation import monday_breadth_opt_selection as search
from yoyo.evaluation import monday_breadth_opt_inputs as inputs
from yoyo.evaluation.monday_breadth_constituent_scope import exclusion_reason

OUT=search.OUT/'exits'
CACHE=search.ROOT/'data/research/monday_breadth_optimization_20261108/exit_fills_v1'
ANCHORS=[(5,30,'N35_99','zero'),(5,30,'N50_99','zero'),(5,30,'N25_100000','any'),
         (5,10,'N10_100000','at_most_four'),(5,30,'N10_100000','at_most_four'),
         (10,30,'N25_100000','ratio_le_25pct'),(30,30,'N25_100000','ratio_le_25pct')]
CASE_OPEN=pd.Timestamp('2026-09-15 18:45',tz='UTC')
CASE_ENTRY=pd.Timestamp('2026-09-15 19:20',tz='UTC')


def member_bridge():
    p=search.OUT/'features/members.csv.gz'
    df=pd.read_csv(p,usecols=['minutes','known','symbol'],parse_dates=['known'])
    return df[~df.symbol.map(lambda s:bool(exclusion_reason(s)))]


def anchor_rules():
    records=[]
    for a in search.ASSETS:
        for w,x,b,d in ANCHORS:
            records.append(dict(asset=a,minutes=w,wait_min=x,breadth=b,diffusion=d,
                                confirmation='none',candidate_id=f'anchor_{a}_{w}_{x}_{b}_{d}'))
    return pd.DataFrame(records)


def rule_definitions(stage):
    anchors=anchor_rules()
    if stage=='anchor':return anchors
    locked=pd.read_csv(search.SEARCH/'locked_rules.csv')
    locked=locked[locked.role.isin(['time_exit_winner','exit_research'])]
    return pd.concat([anchors,locked[anchors.columns]]).drop_duplicates(
            ['asset','minutes','wait_min','breadth','diffusion','confirmation'])


def anchor_frame(w,x):
    full=inputs.features()
    fr=full[(full.minutes==w)&(full.wait_min==x)].copy().sort_values('known')
    fr['market_share_pct']=fr.share28_pct
    fr['prior_shock_age_min']=fr.since_prev_25_lower_shock_min
    for key in ('alt_move','btc_move','reclaim_fraction'):fr[key]=np.nan
    return fr.reset_index(drop=True)


def event_schedule(stage):
    basket=member_bridge(); names=rule_definitions(stage)
    events=[];ids=['minutes','known','decision','initial','new_coins']
    for row in names.itertuples():
        if row.confirmation=='none':
            fr=anchor_frame(row.minutes,row.wait_min)
        else:
            fr,_=inputs.cached_partition(row.minutes,row.wait_min)
        slots,fold=search.time_axes(fr)
        loc=search.rule_indices(fr,row.minutes,row.wait_min,row.breadth,row.diffusion,row.confirmation)
        for h in (72,144,240):
            for epoch in range(4):
                keep=search.admissions(loc[fold[loc]==epoch],slots,h*12,search.ENDS[epoch])
                entries=fr.iloc[keep][ids].copy()
                entries=entries.assign(hours=h,epoch=epoch,candidate_id=row.candidate_id,asset=row.asset)
                events.append(entries)
    e=pd.concat(events,ignore_index=True)
    e['event_i']=np.arange(len(e))
    alt=e[e.asset=='ALT'].merge(basket,on=['minutes','known'],how='inner',validate='many_to_many')
    other=e[e.asset!='ALT'].copy();other['symbol']=other.asset+'USDT'
    bridge=pd.concat([alt,other],ignore_index=True)
    if bridge.duplicated(['event_i','symbol']).any():raise ValueError('Duplicate basket constituent')
    return e,bridge,names


def full_frame(symbol,times):
    from yoyo.evaluation.monday_breadth_opt_payoffs import load_full_ohlc
    pad=pd.Timedelta(days=12)
    needed=[(t-pad,t+pd.Timedelta(days=10)) for t in times]
    raw,proof=load_full_ohlc(symbol,min(times)-pad,max(times)+pd.Timedelta(days=10),needed_intervals=needed)
    if raw.empty:return raw
    return sim.prepare(raw)


def fill_symbol(task):
    sym,raw_times,just_ids=task
    times=pd.DatetimeIndex(raw_times).sort_values().unique()
    all_p=sim.policies(sym=='BTCUSDT')
    destination=CACHE/(sym+'.parquet')
    stored=pd.read_parquet(destination) if destination.is_file() else pd.DataFrame()
    needed=list(range(len(all_p))) if just_ids is None else list(just_ids)
    # Compare every requested timestamp and policy; earlier phases may have evaluated only a subset.
    if not stored.empty:
        coverage=stored[stored.policy_id.isin(needed)].groupby('entry_time').policy_id.nunique()
        times=times.difference(coverage[coverage==len(needed)].index)
    if not len(times):return sym,0
    frame=full_frame(sym,times); pieces=[]
    if not frame.empty:
        for h in sorted(set(all_p[i].hours for i in needed)):
            pids=np.asarray([i for i in needed if all_p[i].hours==h])
            trial=sim.simulate(frame,times,[all_p[i] for i in pids],batch_size=8)
            if len(trial):
                trial['policy_id']=pids[trial.policy_id.to_numpy()]
                pieces.append(trial)
    new=pd.concat(pieces,ignore_index=True) if pieces else pd.DataFrame()
    if not len(new) and not len(stored):return sym,0
    combined=pd.concat([stored,new],ignore_index=True).drop_duplicates(['entry_time','policy_id'],keep='last')
    tmp=destination.with_suffix('.temp')
    combined.to_parquet(tmp,index=False);tmp.replace(destination)
    return sym,len(new)


def run_fills(bridge,workers):
    CACHE.mkdir(parents=True,exist_ok=True)
    demands=bridge[['symbol','decision']].drop_duplicates().groupby('symbol').decision
    work=[(sym,list(t),None) for sym,t in demands]
    with ProcessPoolExecutor(workers) as pool:
        for i,(sym,n) in enumerate(pool.map(fill_symbol,work,chunksize=1),1):
            if i%25==0 or i==len(work):print('Exit fills',i,'/',len(work),sym,n,flush=True)


def aggregate(e,bridge):
    # Stream each constituent's fills. Keep sums and success counts rather than copying many repeated member rows.
    total=len(e);pmax=len(sim.policies(True));shape=(total,pmax)
    acc={k:np.zeros(shape,dtype='float64') for k in
         ('n','net','peak','gain_half','up10','win','legs','ambiguous','points')}
    expected=bridge.groupby('event_i').size().reindex(e.event_i,fill_value=0).to_numpy()
    for sym,g in bridge.groupby('symbol',sort=False):
        path=CACHE/(sym+'.parquet')
        if not path.is_file():continue
        f=pd.read_parquet(path,columns=['entry_time','policy_id','net_pct','net_points','peak_pct','exit_legs','ambiguous'])
        policy_hours=np.array([p.hours for p in sim.policies(sym=='BTCUSDT')])
        f['hours']=policy_hours[f.policy_id]
        m=g[['decision','hours','event_i']].merge(f,left_on=['decision','hours'],right_on=['entry_time','hours'],how='inner')
        i=m.event_i.to_numpy();j=m.policy_id.to_numpy();index=(i,j)
        v=m.net_pct.to_numpy();p=m.peak_pct.to_numpy()
        for k,vals in [('n',1),('net',v),('peak',p),('gain_half',(p>=10)&(v>=.5*p)),('up10',p>=10),
                       ('win',v>0),('legs',m.exit_legs.to_numpy()),('ambiguous',m.ambiguous.to_numpy()),
                       ('points',m.net_points.to_numpy())]:
            acc[k][index]+=vals
    labels=sim.policies(True);hrs=np.array([p.hours for p in labels])
    matching=(e.hours.to_numpy()[:,None]==hrs[None,:])
    matching[:,len(sim.policies()):]&=(e.asset.to_numpy()[:,None]=='BTC')
    i,j=np.where(matching)
    n=acc['n'][i,j]
    rows=e.iloc[i].reset_index(drop=True).copy()
    rows['policy_id']=j
    for k in ('net','peak','points','win','legs','ambiguous'):
        values=acc[k][i,j]/np.maximum(1,n)
        rows[k]=np.where((n>0)&(n==expected[i]),values,np.nan)
    rows['up10']=acc['up10'][i,j];rows['gain_half']=acc['gain_half'][i,j]
    rows['observed']=n.astype(int);rows['expected']=expected[i]
    rows.rename(columns={'net':'net_pct','peak':'peak_pct'},inplace=True)
    return rows


def save_large(df,path):
    path.mkdir(parents=True,exist_ok=True)
    for a,s in df.groupby('asset'):
        s.to_parquet(path/f'{a}.parquet',index=False)


def main():
    cli=argparse.ArgumentParser()
    cli.add_argument('stage',choices=['anchor','all'])
    cli.add_argument('--workers',type=int,default=3)
    args=cli.parse_args();OUT.mkdir(parents=True,exist_ok=True)
    e,bridge,names=event_schedule(args.stage)
    print('staged',len(e),'events',len(bridge),'member schedules',flush=True)
    names.to_csv(OUT/(args.stage+'_entries.csv'),index=False)
    run_fills(bridge,args.workers)
    result=aggregate(e,bridge)
    save_large(result,OUT/(args.stage+'_event_fills'))
    pd.DataFrame([dict(policy_id=i,**asdict(p),key=p.key) for i,p in enumerate(sim.policies(True))]).to_csv(OUT/'policies.csv',index=False)


if __name__=='__main__':
    main()
