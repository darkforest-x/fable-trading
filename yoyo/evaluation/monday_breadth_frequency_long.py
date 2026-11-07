"""Increase breadth reversal entry frequency with a flat-account ETH admission schedule.

Signal inputs are previously computed on closed 10-minute and following 120-minute
windows (initial unique touches, trailing 28-day active share, new symbols and
BTC close return). Exit time comes from the original native VectorBT policy 67.
Earlier exit frees the account only after the exit bar and the one-day entry gap.
"""
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_exits as sim
from yoyo.evaluation import monday_breadth_opt_selection as old

EXP = old.OUT.parent / 'exp-monday-breadth-frequency-20261108-v1'
OUT = EXP / 'long'
CACHE = old.ROOT / 'data/research/monday_breadth_frequency_20261108/eth_67.parquet'
CUTOFF = pd.Timestamp('2026-10-28', tz='UTC')
POLICY = 67
TIERS = (36, 72)


def save_fill_cache(times):
    policy = sim.policies(False)[POLICY]
    assert (policy.kind, policy.hours, policy.stop_pct, policy.activate_pct, policy.width) == ('trend_1h',144,6,5,50)
    path = study.CACHE / 'ETHUSDT.parquet'
    parts = [pd.read_parquet(path, filters=[('policy_id','=',POLICY)])]
    if CACHE.exists():
        parts.append(pd.read_parquet(CACHE))
    fills = pd.concat(parts, ignore_index=True).drop_duplicates('entry_time')
    missing = pd.DatetimeIndex(times).unique().difference(fills.entry_time)
    if len(missing):
        frame = study.full_frame('ETHUSDT',missing)
        new = sim.simulate(frame, missing, [policy], batch_size=64)
        if len(new):
            new['policy_id'] = POLICY
            fills = pd.concat([fills,new],ignore_index=True)
    CACHE.parent.mkdir(parents=True,exist_ok=True)
    tmp = CACHE.with_suffix('.tmp')
    fills.sort_values('entry_time').to_parquet(tmp,index=False)
    tmp.replace(CACHE)
    return fills.set_index('entry_time')


def old_counts():
    rules = pd.read_csv(study.OUT/'locked_rules.csv')
    frozen = pd.read_parquet(study.OUT/'locked_events.parquet')
    rows = []
    for r in rules[rules.role.eq('dynamic_primary')].itertuples():
        frame,_ = old.inputs.cached_partition(r.minutes,r.wait_min)
        slots,epoch = old.time_axes(frame)
        ids = old.rule_indices(frame,r.minutes,r.wait_min,r.breadth,r.diffusion,r.confirmation)
        active = frozen[(frozen.candidate_id==r.candidate_id)&frozen.net_pct.notna()]
        for p in range(4):
            idx = ids[epoch[ids]==p]
            in_period = idx[slots[idx]+int(r.hold_hours)*12 < old.ENDS[p]]
            admitted = old.admissions(idx,slots,int(r.hold_hours)*12,old.ENDS[p])
            for year,sub in frame.iloc[idx].groupby(frame.iloc[idx].decision.dt.year):
                q = active[active.decision.dt.year.eq(year)]
                source = set(sub.index)
                eligible,approved = len(source.intersection(in_period)),len(source.intersection(admitted))
                rows.append(dict(asset=r.asset,year=int(year),raw=len(sub),cross_split=len(sub)-eligible,
                    reserved=eligible-approved,scheduled=approved,price_available=len(q),
                    unavailable=approved-len(q)))
            assert set(frame.iloc[admitted].decision).issuperset(set(active[active.epoch.eq(p)].decision))
    pd.DataFrame(rows).to_csv(OUT/'previous_signal_counts.csv',index=False)


def definitions(frame):
    n,y,b = frame.initial,frame.new_coins,frame.btc_move
    eligible = frame.monitor_fraction.eq(1)&frame.decision.lt(CUTOFF)
    breadth = {f'n{v}':n.ge(v) for v in (2,5,10)}
    breadth.update({f'share{v:g}':n.ge(2)&frame.market_share_pct.ge(v) for v in (.5,1,2)})
    follow = {'any':y.ge(0),'1to4':y.between(1,4),'le4':y.le(4),'leN':y.le(n),'leQuarterN':4*y<=n}
    btc = {'any':n.ge(0),**{f'ge{v:g}':b.ge(v) for v in (0,.25,.5,1)}}
    for br,di,cf in itertools.product(breadth,follow,btc):
        yield (br,di,cf),np.flatnonzero(eligible & breadth[br] & follow[di] & btc[cf])


def schedule(ids,slots,ends,epochs,mode):
    out=[]
    for p in range(4):
        next_slot=-1
        for i in ids[epochs[ids]==p]:
            start=slots[i]
            if start+144*12>=old.ENDS[p] or start<next_slot:
                continue
            out.append(i)
            if mode=='reserved' or ends[i]<start:
                next_slot=start+144*12
            else:
                next_slot=max(start+24*12,ends[i]+1)
    return np.array(out,dtype=int)


def stats(part):
    return old.describe(part)


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    old_counts()
    fr,_=old.inputs.cached_partition(10,120)
    fr=fr[fr.decision<CUTOFF].copy().reset_index(drop=True)
    slots,epochs=old.time_axes(fr)
    defs=dict(definitions(fr))
    original=old.rule_indices(fr,10,120,'share_2','one_to_four','btc_move_ge1')
    union=np.unique(np.concatenate([*defs.values(),original]))
    fills=save_fill_cache(fr.iloc[union].decision)
    selected=fr[['minutes','known','decision','initial','new_coins','btc_move','market_share_pct']].copy()
    selected['epoch']=epochs
    selected=selected.join(fills[['exit_time','entry_open','exit_vwap','net_pct','peak_pct']],on='decision')
    ends=pd.DatetimeIndex(selected.exit_time).as_unit('s').asi8//300
    schedules={}
    development=[]
    for keys,ids in defs.items():
        keep=schedule(ids,slots,ends,epochs,'flat')
        schedules[keys]=keep
        part=selected.iloc[keep]
        row=dict(breadth=keys[0],diffusion=keys[1],confirmation=keys[2])
        for p in range(3):
            st=stats(part[part.epoch.eq(p)])
            row.update({f'{k}{p}':st[k] for k in ('n','months','mean','lcb','win','dd')})
        development.append(row)
    dev=pd.DataFrame(development)
    dev['score']=dev[['lcb0','lcb1','lcb2']].min(axis=1,skipna=False)
    dev['quality']=(dev[['mean0','mean1','mean2']].min(axis=1)>0)&(dev.dd2<=35)
    rules=[];events=[];comparison=[]
    for tier in TIERS:
        gate=(dev.n0>=50)&(dev.n1>=2*tier)&(dev.n2>=tier)&(dev.months2>=8)
        dev[f'frequency_{tier}']=gate
        pool=dev[gate].sort_values(['quality','score','mean2','breadth','diffusion','confirmation'],ascending=[False,False,False,True,True,True],kind='stable')
        if pool.empty:continue
        row=pool.iloc[0]; keys=tuple(row[k] for k in ('breadth','diffusion','confirmation'))
        cid=f'ETH_flat_{tier}'
        rules.append(dict(candidate_id=cid,asset='ETH',minutes=10,wait_min=120,breadth=keys[0],diffusion=keys[1],
            confirmation=keys[2],role='dynamic_primary',hold_hours=144,policy_id=POLICY,frequency_target=tier,
            eligible=bool(row.quality),selection_score=row.score))
        e=selected.iloc[schedules[keys]].copy()
        e=e.assign(candidate_id=cid,asset='ETH',hours=144,policy_id=POLICY,expected=1)
        events.append(e)
        for mode in ('flat','reserved'):
            pos=schedule(defs[keys],slots,ends,epochs,mode)
            comparison.append(selected.iloc[pos].assign(candidate_id=cid,mode=mode))
    for mode in ('flat','reserved'):
        pos=schedule(original,slots,ends,epochs,mode)
        part=selected.iloc[pos].assign(candidate_id='original_ETH',mode=mode)
        comparison.append(part)
        if mode=='reserved':
            ref=pd.read_parquet(study.OUT/'locked_events.parquet').query("candidate_id=='dynamic_primary_ETH'").sort_values('decision')
            got=part.dropna(subset=['net_pct']).sort_values('decision')
            assert list(ref.decision)==list(got.decision) and np.allclose(ref.net_pct,got.net_pct)
    dev.to_csv(OUT/'development.csv',index=False)
    pd.DataFrame(rules).to_csv(OUT/'locked_rules.csv',index=False)
    pd.concat(events,ignore_index=True).to_parquet(OUT/'locked_events.parquet',index=False)
    all_events=pd.concat(comparison,ignore_index=True).dropna(subset=['net_pct'])
    all_events.to_parquet(OUT/'admission_comparison.parquet',index=False)
    totals=[]
    for (cid,mode),g in all_events.groupby(['candidate_id','mode']):
        for label,part in [('all',g),*[(str(k),v) for k,v in g.groupby(g.decision.dt.year)]]:
            totals.append(dict(candidate_id=cid,mode=mode,year=label,**stats(part)))
    pd.DataFrame(totals).to_csv(OUT/'yearly.csv',index=False)
    print(pd.DataFrame(rules).to_string(index=False),flush=True)


if __name__=='__main__':run()
