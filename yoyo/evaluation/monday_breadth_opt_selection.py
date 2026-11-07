"""Chronological pre-registered search on VectorBT event returns.

The only decision inputs are frozen close-known initial counts, distinct subsequent
first touches, monitored duration, week-breadth denominator, time since past shocks
and own/benchmark closes strictly before entry. Future returns are used for
selection only within the disclosed historical folds. A received new signal never
overlaps an admitted position; entry spacing is at least one day.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from numba import njit

from yoyo.evaluation import monday_breadth_opt_inputs as inputs

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'experiments/active/exp-monday-breadth-optimization-20261108-v1'
SEARCH = OUT/'search'
CFG = json.loads((OUT/'config.json').read_text())
ASSETS = ('BTC','ETH','SOL','ALT')
HOLDS = tuple(CFG['H_hours'])
SPLITS = np.array([pd.Timestamp(t,tz='UTC').timestamp()//300 for t in CFG['splits_utc']],dtype='int64')
ENDS = np.r_[SPLITS,np.iinfo('int64').max//2]
RANGES = [('N%d_%d'%(lo,hi),lo,hi) for lo,hi in CFG['breadth_count_ranges']]
BREADTH = [name for name,_,_ in RANGES]+[f'share_{n}' for n in CFG['breadth_share_min_pct']]


def rules(frame, w, x):
    n=frame.initial.to_numpy(); y=frame.new_coins.to_numpy()
    a=frame.first_half_new.to_numpy(); b=frame.last_half_new.to_numpy()
    share=frame.market_share_pct.to_numpy()
    breadth={name:(n>=lo)&(n<=hi) for name,lo,hi in RANGES}
    breadth.update({f'share_{v}':share>=v for v in CFG['breadth_share_min_pct']})
    all_rows=np.ones(len(frame),dtype=bool)
    diffusion=dict(any=all_rows,zero=y==0,one_to_four=(y>=1)&(y<=4),five_to_nine=(y>=5)&(y<=9),
                   ten_plus=y>=10,at_most_four=y<=4,ratio_le_25pct=4*y<=n,
                   ratio_25_to_100pct=(4*y>n)&(y<=n),ratio_gt_100pct=y>n)
    if x:  # Compare rates rather than unequal half lengths on odd native-bar waits.
        front_bars=(x//5)//2; back_bars=x//5-front_bars
        if front_bars:
            diffusion.update(front_only=(a>0)&(b==0),back_only=(a==0)&(b>0),
                             slowing=a*back_bars>b*front_bars,accelerating=a*back_bars<b*front_bars)
    else:
        diffusion={'any':all_rows}
    r=frame.reclaim_fraction.to_numpy(); move=frame.alt_move.to_numpy(); btc=frame.btc_move.to_numpy()
    prior=frame.prior_shock_age_min.to_numpy()
    conf=dict(none=all_rows,reclaim_lt25=r<.25,reclaim_25to75=(r>=.25)&(r<.75),
              reclaim_ge50=r>=.5,reclaim_ge75=r>=.75,alt_move_negative=move<0,
              alt_move_0to2=(move>=0)&(move<2),alt_move_ge2=move>=2,
              fresh_cluster=frame.last_touch_age_min.to_numpy()<=5,
              prior_shock_6h=prior<=360,no_prior_shock_24h=prior>=1440,
              btc_move_negative=btc<0,btc_move_0to1=(btc>=0)&(btc<1),btc_move_ge1=btc>=1)
    if not x:
        for k in ('alt_move_negative','alt_move_0to2','alt_move_ge2','btc_move_negative','btc_move_0to1','btc_move_ge1'):
            conf.pop(k)
    return breadth,diffusion,conf


@njit(cache=True)
def admissions(indices, slots, hold_slots, epoch_end):
    selected=np.empty(len(indices),dtype=np.int64)
    use=0; next_slot=-1
    for i in indices:
        t=slots[i]
        if t+hold_slots>=epoch_end: continue
        if t>=next_slot:
            selected[use]=i
            use+=1
            next_slot=t+max(288,hold_slots)
    return selected[:use]


@njit(cache=True)
def score_one(indices, returns):
    n=0; win=0; raw=0.; capped=0.; squared=0.
    for i in indices:
        val=returns[i]
        if not np.isfinite(val): continue
        n+=1; win+=int(val>0); raw+=val
        v=max(-20.,min(20.,val)); capped+=v; squared+=v*v
    if n==0: return (0.,np.nan,np.nan,np.nan,np.nan)
    mean=capped/n
    se=np.sqrt(max(0.,squared-n*mean*mean)/(max(1,n-1)*n))
    return (float(n),raw/n,100.*win/n,mean,mean-1.28*se)


@njit(cache=True)
def score_matrix(indices,slots,fold,ret,holds,ends):
    out=np.zeros((len(holds),ret.shape[2],2,5))
    for h in range(len(holds)):
        for period in range(2):
            rows=indices[fold[indices]==period]
            selected=admissions(rows,slots,holds[h]*12,ends[period])
            for a in range(ret.shape[2]):
                out[h,a,period,:]=score_one(selected,ret[:,h,a])
    return out


def time_axes(frame):
    times=pd.DatetimeIndex(frame.decision).as_unit('s').asi8//300
    assert len(times)==len(np.unique(times)) and (np.diff(times)>0).all()
    return times,np.searchsorted(SPLITS,times,side='right')


def tag(w,x):
    return f'w{w:03d}_x{x:03d}'


def run_partition(w,x):
    name=tag(w,x)
    directory=SEARCH/'development';directory.mkdir(parents=True,exist_ok=True)
    out=directory/(name+'.parquet')
    maps=directory/(name+'_rules.parquet')
    frame,ret=inputs.partition(w,x)
    slots,fold=time_axes(frame)
    breadth,follow,confirm=rules(frame,w,x)
    key_map={}; mapping=[]; scored=[]; canonical=[]
    complete=frame.monitor_fraction.eq(1).to_numpy()&(fold<2)
    for name1,mask1 in breadth.items():
        for name2,mask2 in follow.items():
            base=complete&mask1&mask2
            for name3,mask3 in confirm.items():
                ids=np.flatnonzero(base&mask3)
                signature=ids.tobytes()
                if signature not in key_map:
                    ident=len(key_map)
                    key_map[signature]=ident
                    canonical.append((name1,name2,name3))
                    scored.append(score_matrix(ids,slots,fold,ret,np.asarray(HOLDS),ENDS))
                mapping.append((name1,name2,name3,key_map[signature]))
    pd.DataFrame(mapping,columns=['breadth','diffusion','confirmation','pattern']).to_parquet(maps,index=False)
    records=np.array(scored).reshape(len(scored)*len(HOLDS)*len(ASSETS),10)
    cols=[f'{period}_{metric}' for period in ('a','b') for metric in ('n','mean','win','clipped','lcb')]
    df=pd.DataFrame(records,columns=cols)
    canon=pd.DataFrame(canonical,columns=['breadth','diffusion','confirmation'])
    dimensions=pd.DataFrame({'pattern':np.repeat(np.arange(len(scored)),len(HOLDS)*len(ASSETS)),
        'hold_hours':np.tile(np.repeat(HOLDS,len(ASSETS)),len(scored)), 'asset':np.tile(ASSETS,len(scored)*len(HOLDS))})
    df=pd.concat([dimensions.merge(canon,left_on='pattern',right_index=True,validate='many_to_one'),df],axis=1)
    df.insert(0,'minutes',w);df.insert(1,'wait_min',x)
    df.to_parquet(out,index=False)
    return len(mapping),len(scored)


def dev_rank():
    pieces=[]; audit=[]
    for w in CFG['W_minutes']:
        for x in CFG['X_minutes']:
            file=SEARCH/'development'/(tag(w,x)+'.parquet')
            s=pd.read_parquet(file)
            ok=(s.a_n>=12)&(s.b_n>=12)&((s.a_n+s.b_n)>=30)&(s.a_mean>0)&(s.b_mean>0)
            ok&=(s.a_n*s.a_win+s.b_n*s.b_win)>=50*(s.a_n+s.b_n)
            q=s[ok].copy()
            q['training_floor']=q[['a_lcb','b_lcb']].min(axis=1)
            q['training_win']=q[['a_win','b_win']].min(axis=1)
            q['training_mean']=q[['a_clipped','b_clipped']].min(axis=1)
            q['complexity']=(q.diffusion!='any').astype(int)+(q.confirmation!='none').astype(int)
            pieces.append(q)
            n=len(pd.read_parquet(file.with_name(tag(w,x)+'_rules.parquet')))
            audit.append(dict(minutes=w,wait_min=x,combinations=n*len(HOLDS)*len(ASSETS),
                              distinct_patterns_times_assets_holds=len(s),train_admissible=len(q)))
    pd.DataFrame(audit).to_csv(SEARCH/'search_counts.csv',index=False)
    full=pd.concat(pieces,ignore_index=True)
    names=['training_floor','training_win','training_mean']
    selected=[]
    # Prevent adjacent decision paths with redundant parameterizations from occupying the entire shortlist.
    # Exact admission signatures use only the two development epochs, with a separate namespace per asset and hold.
    for (asset,w),group in full.groupby(['asset','minutes']):
        seen_by_rank={}
        for metric in names:
            picked=[]; seen=set()
            order=group.sort_values([metric,'training_floor','complexity','wait_min','pattern','hold_hours']
                                    if metric!='training_floor' else [metric,'complexity','wait_min','pattern','hold_hours'],
                                    ascending=[False,False,True,True,True,True] if metric!='training_floor' else [False,True,True,True,True],kind='stable')
            for r in order.itertuples():
                fr,ret=inputs.cached_partition(w,r.wait_min)
                slots,fold=time_axes(fr)
                ids=rule_indices(fr,w,r.wait_min,r.breadth,r.diffusion,r.confirmation)
                held=[admissions(ids[fold[ids]==p],slots,r.hold_hours*12,ENDS[p]) for p in (0,1)]
                sig=(r.hold_hours,slots[np.concatenate(held)].tobytes())
                if sig in seen:continue
                picked.append(r.Index);seen.add(sig)
                if len(picked)==6:break
            seen_by_rank[metric]=picked
        selected.extend(i for ranks in seen_by_rank.values() for i in ranks)
    short=full.loc[sorted(set(selected))].reset_index(drop=True)
    short.to_csv(SEARCH/'dev_shortlist.csv',index=False)
    return short


def rule_indices(frame,w,x,br,di,co):
    b,d,c=rules(frame,w,x)
    return np.flatnonzero(frame.monitor_fraction.eq(1).to_numpy()&b[br]&d[di]&c[co])


def evaluate_rule(r,frame,ret):
    slots,fold=time_axes(frame); ids=rule_indices(frame,r.minutes,r.wait_min,r.breadth,r.diffusion,r.confirmation)
    asset_i=ASSETS.index(r.asset); h=HOLDS.index(r.hold_hours)
    pieces=[]
    for p in range(4):
        kept=admissions(ids[fold[ids]==p],slots,r.hold_hours*12,ENDS[p])
        part=frame.iloc[kept][['known','decision','initial','new_coins']].copy()
        part['epoch']=p;part['net_pct']=ret[kept,h,asset_i];pieces.append(part)
    return pd.concat(pieces,ignore_index=True)


def describe(group):
    v=group.dropna(subset=['net_pct']).sort_values('decision')
    vals=v.net_pct.to_numpy()
    if not len(vals):
        return dict(n=0,weeks=0,months=0,win=np.nan,mean=np.nan,median=np.nan,worst=np.nan,dd=np.nan,
                    clipped=np.nan,lcb=np.nan,exbest=np.nan,exmonth=np.nan,growth=np.nan,stress=np.nan)
    clipped=np.clip(vals,-20,20);lcb=clipped.mean()-1.28*clipped.std(ddof=1)/np.sqrt(len(vals)) if len(vals)>1 else np.nan
    month=v.decision.dt.strftime('%Y-%m')
    months=vals.copy(); by_month=pd.Series(months).groupby(month.to_numpy()).sum()
    mask=month.ne(by_month.idxmax()).to_numpy()
    cash=np.r_[1.,np.cumprod(1+vals/100)]
    return dict(n=len(vals),weeks=v.decision.dt.strftime('%G-%V').nunique(),months=month.nunique(),
                win=100*np.mean(vals>0),mean=vals.mean(),median=np.median(vals),worst=vals.min(),
                dd=100*(1-np.min(cash/np.maximum.accumulate(cash))),clipped=clipped.mean(),lcb=lcb,
                exbest=np.delete(vals,vals.argmax()).mean() if len(vals)>1 else np.nan,
                exmonth=vals[mask].mean() if mask.any() else np.nan,growth=100*(cash[-1]-1),stress=vals.mean()-.20)


def freeze(short):
    results=[]
    for r in short.itertuples():
        frame,ret=inputs.cached_partition(r.minutes,r.wait_min)
        # Validation has access only to signals before 2026, and every trade crossing the boundary is purged.
        v=frame.decision.lt(pd.Timestamp('2026-01-01',tz='UTC')).to_numpy()
        events=evaluate_rule(r,frame[v],ret[v])
        stats=describe(events[events.epoch==2]); row=r._asdict(); row.pop('Index')
        row.update({'v_'+k:val for k,val in stats.items()})
        row['validation_pass']=(stats['n']>=10 and stats['months']>=4 and stats['win']>=50
                                and stats['mean']>0 and stats['dd']<=35)
        row['joint_score']=min(r.training_floor,stats['lcb']) if np.isfinite(stats['lcb']) else np.nan
        results.append(row)
    s=pd.DataFrame(results)
    s.to_csv(SEARCH/'validation.csv',index=False)
    eligible=s[s.validation_pass].sort_values(['joint_score','v_win','training_floor','complexity'],
                                              ascending=[False,False,False,True],kind='stable')
    winners=eligible.groupby('asset',sort=False).head(1).copy()
    winners['role']='time_exit_winner'
    # Preserve distinct alternatives for dynamic exits without any later-year return entering selection.
    runners=eligible.drop_duplicates(['asset','minutes','wait_min','breadth','diffusion','confirmation'])
    runners=runners.groupby('asset',sort=False).head(3).copy()
    runners['role']='exit_research'
    bases=[]
    for a in ASSETS:
        for br in ('N1_100000','N35_99','N50_99'):
            bases.append(dict(asset=a,minutes=5,wait_min=30,hold_hours=144,breadth=br,
                              diffusion='any' if br=='N1_100000' else 'zero',confirmation='none',role='baseline'))
    frozen=pd.concat([winners,runners,pd.DataFrame(bases)],ignore_index=True)
    frozen['candidate_id']=[f'{row.role}_{row.asset}_{i:02d}' for i,row in enumerate(frozen.itertuples())]
    frozen.to_csv(SEARCH/'locked_rules.csv',index=False)
    return frozen


def forward(frozen):
    result=[];audit=[]
    for r in frozen.itertuples():
        fr,re=inputs.cached_partition(r.minutes,r.wait_min)
        e=evaluate_rule(r,fr,re).assign(candidate_id=r.candidate_id,asset=r.asset)
        result.append(e)
        for p in range(4):
            audit.append(dict(candidate_id=r.candidate_id,asset=r.asset,epoch=p,**describe(e[e.epoch==p])))
    pd.concat(result).to_parquet(SEARCH/'locked_events.parquet',index=False)
    pd.DataFrame(audit).to_csv(SEARCH/'locked_evaluation.csv',index=False)


def main():
    args=argparse.ArgumentParser()
    args.add_argument('stage',choices=['grid','select','forward'])
    args.add_argument('--minutes',type=int)
    a=args.parse_args()
    SEARCH.mkdir(parents=True,exist_ok=True)
    if a.stage=='grid':
        for w in ([a.minutes] if a.minutes else CFG['W_minutes']):
            for x in CFG['X_minutes']:
                counts=run_partition(w,x)
                print(f'W={w} X={x} {counts}',flush=True)
    elif a.stage=='select':
        freeze(dev_rank())
    else:
        forward(pd.read_csv(SEARCH/'locked_rules.csv'))


if __name__=='__main__':
    main()
