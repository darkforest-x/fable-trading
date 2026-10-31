"""Aggregate native reclaim fills and select candidates chronologically.

All metrics here consume saved framework results. No price crossings, new
trades, model fitting or post-hoc threshold search occur. Parameters are
chosen on exits before 2025 and then evaluated on later years. Previously
inspected history remains research evidence, not a pristine unseen test.
"""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
from yoyo.evaluation.top20_12h_strategies import EXP,candidates,dump


def combine(legs):
    """Compose fixed candidate recipes from saved native full/half-size legs."""
    rows=[]
    for cfg in candidates():
        x=legs[legs['mode'].eq(cfg['mode']) & legs.days.eq(cfg['days'])].copy()
        if cfg['target']=='partial':
            x=x[x.target_name.isin(['partial_5R','partial_opposite'])]
            for _,g in x.groupby(['event_id','path'],sort=False):
                if len(g)!=2:continue
                r=g.iloc[0].to_dict()
                for col in ['net_r','gross_r','fee_r','net_bp']:r[col]=float(g[col].sum())
                r['closed']=bool(g.closed.all());r['exit_time']=str(pd.to_datetime(g.exit_time,utc=True).max())
                r['reason']='partial';r['weight']=1.;r['config']=cfg['config']
                rows.append(r)
        else:
            x=x[x.target_name.eq(cfg['target'])]
            if cfg['space_filter']:
                near=legs[legs['mode'].eq(cfg['mode']) & legs.days.eq(14) & legs.target_name.eq('near')]
                valid=near[near.target_r.ge(5)][['event_id','path']]
                x=x.merge(valid,on=['event_id','path'],validate='one_to_one')
            x['config']=cfg['config'];rows.extend(x.to_dict('records'))
    paths=pd.DataFrame(rows)
    worst=[]
    for _,g in paths.groupby(['event_id','config'],sort=False):
        r=g.loc[g.net_r.idxmin()].to_dict()
        r['best_path_net_r']=float(g.net_r.max())
        r['path_difference_r']=float(g.net_r.max()-g.net_r.min())
        r['both_paths_complete']=bool(len(g)==2 and g.closed.all())
        r['closed']=r['both_paths_complete']
        # Conservative timing for purging and closure bookkeeping across paths.
        r['entry_time']=str(pd.to_datetime(g.entry_time,utc=True).min())
        r['exit_time']=str(pd.to_datetime(g.exit_time,utc=True).max())
        worst.append(r)
    return paths,pd.DataFrame(worst)


def stats(g):
    n=len(g)
    if not n:return dict(n=0)
    r=g.net_r.to_numpy(float);win=r>0
    # Realized closed-trade sequence only, not mark-to-market account equity.
    s=g.assign(t=pd.to_datetime(g.exit_time,utc=True)).groupby('t').net_r.sum().sort_index().cumsum()
    equity=np.r_[0.,s.to_numpy()];dd=np.maximum.accumulate(equity)-equity
    ordered=g.assign(t=pd.to_datetime(g.exit_time,utc=True)).sort_values(['t','event_id']).net_r.to_numpy()
    longest=run=0
    for v in ordered:
        run=run+1 if v<=0 else 0;longest=max(run,longest)
    losses=-r[r<0].sum();gains=r[r>0].sum()
    months=pd.to_datetime(g.exit_time,utc=True).dt.strftime('%Y-%m')
    monthly=g.groupby(months).net_r.sum()
    coins=g.groupby('symbol').net_r.sum().sort_values(ascending=False)
    z=1.95996398454;p=float(win.mean());den=1+z*z/n
    center=(p+z*z/(2*n))/den;half=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    weeks=g.groupby('week').net_r.sum()
    return dict(n=n,win_pct=p*100,win_wilson_low_pct=(center-half)*100,
        win_wilson_high_pct=(center+half)*100,net_r=float(r.sum()),mean_net_r=float(r.mean()),
        median_net_r=float(np.median(r)),gross_r=float(g.gross_r.sum()),fee_r=float(g.fee_r.sum()),
        profit_factor=float(gains/losses) if losses else None,
        realized_r_drawdown=float(dd.max()),longest_loss_streak=longest,
        mean_net_bp=float(g.net_bp.mean()),worst_month_r=float(monthly.min()),
        best_coin=coins.index[0],best_coin_net_r=float(coins.iloc[0]),
        excluding_best_coin_net_r=float(r.sum()-coins.iloc[0]),
        excluding_best_week_net_r=float(r.sum()-weeks.max()),
        independent_weeks=len(weeks),path_sensitive=int(g.path_difference_r.gt(1e-9).sum()),
        before_current_listing=int(g.before_current_listTime.fillna(False).sum()))


def periods(x):
    t=pd.to_datetime(x.entry_time,utc=True);e=pd.to_datetime(x.exit_time,utc=True)+pd.Timedelta(minutes=15)
    a=pd.Timestamp('2025-01-01',tz='UTC');b=pd.Timestamp('2026-01-01',tz='UTC')
    signal=pd.to_datetime(x.signal_close,utc=True) if 'signal_close' in x else t
    full_window=signal+pd.Timedelta(days=29)
    mature=full_window.le(pd.to_datetime(x.source_end,utc=True)) if 'source_end' in x else pd.Series(True,index=x.index)
    valid=x.closed&mature
    return {'all_observed':x.closed,'all':valid,'selection_pre2025':valid&(signal<a)&(full_window<a)&(e<a),
            'evaluation_2025':valid&(signal>=a)&(full_window<b)&(e<b),
            'evaluation_2026':valid&(signal>=b),
            'evaluation_2025_2026':valid&(signal>=a)}


def bootstrap_pairs(p,seed=20261101):
    if p.empty:return dict(n=0)
    week=p.groupby('week').agg(total=('excess','sum'),n=('excess','size'))
    values=week.total.to_numpy();counts=week.n.to_numpy();rng=np.random.default_rng(seed)
    idx=rng.integers(0,len(week),(2000,len(week)))
    boot=values[idx].sum(axis=1)/counts[idx].sum(axis=1)
    signs=rng.choice([-1,1],size=(2000,len(week)))
    perm=(signs*values).sum(axis=1)/counts.sum()
    observed=float(p.excess.mean())
    return dict(n=len(p),weeks=len(week),signal_mean_r=float(p.signal.mean()),
        random_mean_r=float(p.random.mean()),excess_mean_r=observed,
        ci_low=float(np.quantile(boot,.025)),ci_high=float(np.quantile(boot,.975)),
        p_two_sided=float((1+np.sum(np.abs(perm)>=abs(observed)))/2001))


def ablations(signal):
    """Preserve equal event cohorts for partial exits and entry comparisons."""
    pairs=[('A_limit_5R_14d','B_618_5R_14d'),('A_limit_5R_14d','B_705_5R_14d'),
           ('B_618_5R_14d','B_705_5R_14d'),('A_limit_5R_14d','C_sweep_5R_14d')]
    for mode in ['A_limit','B_618','B_705','C_sweep']:
        for other in [f'{mode}_5R_7d',f'{mode}_5R_28d',f'{mode}_partial_14d']:
            pairs.append((f'{mode}_5R_14d',other))
        pairs.append((f'{mode}_opposite_14d',f'{mode}_partial_14d'))
    out=[]
    for period,mask in periods(signal).items():
        x=signal[mask]
        for baseline,candidate in pairs:
            a=x[x.config.eq(baseline)];b=x[x.config.eq(candidate)]
            common=set(a.event_id)&set(b.event_id)
            aa=a[a.event_id.isin(common)];bb=b[b.event_id.isin(common)]
            out.append(dict(period=period,baseline=baseline,candidate=candidate,
                            common_events=len(common),baseline_stats=stats(aa),candidate_stats=stats(bb)))
    return out


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    dests=sorted((EXP/'symbols').glob('*/done.json'))
    assert len(dests)==20, 'Complete all symbols before ranking'
    legs=pd.concat([pd.read_csv(p.parent/'legs.csv.gz',float_precision='round_trip') for p in dests if json.loads(p.read_text())['legs']],ignore_index=True)
    entries=pd.concat([pd.read_csv(p.parent/'entries.csv.gz',float_precision='round_trip') for p in dests],ignore_index=True)
    coverage=pd.concat([pd.read_csv(p.parent/'coverage.csv') for p in dests],ignore_index=True)
    paths,trades=combine(legs)
    source_ends={json.loads(p.read_text())['symbol']:str(pd.Timestamp(json.loads(p.read_text())['source']['last_source'])+pd.Timedelta(minutes=15)) for p in dests}
    trades['source_end']=trades.symbol.map(source_ends)
    paths.to_csv(EXP/'path_results.csv.gz',index=False)
    trades.to_csv(EXP/'trades.csv.gz',index=False)
    rows=[];comparisons=[];allpairs=[]
    signal=trades[~trades.control];random=trades[trades.control]
    for period,mask in periods(trades).items():
        selected=trades[mask]
        for name,g in selected[~selected.control].groupby('config'):
            st=stats(g);rows.append(dict(period=period,config=name,**st))
            controls=selected[selected.control & selected.config.eq(name)]
            means=controls.groupby('pair_id').net_r.agg(['mean','size'])
            p=g[['event_id','week','net_r']].merge(means,left_on='event_id',right_index=True)
            p=p.rename(columns={'net_r':'signal','mean':'random','size':'control_count'})
            p['excess']=p.signal-p.random;p['config']=name;p['period']=period
            allpairs.extend(p.to_dict('records'))
            comparisons.append(dict(period=period,config=name,matched_fraction=len(p)/len(g),**bootstrap_pairs(p)))
    headline=pd.DataFrame(rows);paired=pd.DataFrame(comparisons)
    # Holm correction within each declared evaluation period across all candidates.
    paired['p_holm']=np.nan
    for _,g in paired[paired.n.gt(0)].groupby('period'):
        order=g.p_two_sided.sort_values().index;m=len(order)
        adjusted=np.maximum.accumulate([min(1.,paired.loc[j,'p_two_sided']*(m-k)) for k,j in enumerate(order)])
        paired.loc[order,'p_holm']=adjusted
    headline.to_csv(EXP/'headline.csv',index=False);paired.to_csv(EXP/'matched_summary.csv',index=False)
    pd.DataFrame(allpairs).to_csv(EXP/'matched_pairs.csv.gz',index=False)
    yearly=[];coin=[]
    valid=signal[periods(signal)['all']].copy();valid['year']=pd.to_datetime(valid.entry_time,utc=True).dt.year
    for (name,year),g in valid.groupby(['config','year']):yearly.append(dict(config=name,year=int(year),**stats(g)))
    for (name,symbol),g in valid.groupby(['config','symbol']):coin.append(dict(config=name,symbol=symbol,**stats(g)))
    pd.DataFrame(yearly).to_csv(EXP/'by_year.csv',index=False);pd.DataFrame(coin).to_csv(EXP/'by_symbol.csv',index=False)
    dump(EXP/'same_cohort_comparisons.json',ablations(signal))
    selection=headline[(headline.period=='selection_pre2025') & headline.n.ge(cfg['minimum_selection_trades'])].copy()
    winners={}
    definitions=[('win_rate_at_5R','win_pct',True),('total_net_R','net_r',False),('return_over_realized_drawdown','efficiency',False)]
    for goal,metric,five_only in definitions:
        pool=selection.copy()
        if five_only:pool=pool[pool.config.str.contains('_5R_')]
        if metric=='efficiency':
            pool=pool[pool.net_r.gt(0)&pool.realized_r_drawdown.gt(0)]
            pool['efficiency']=pool.net_r/pool.realized_r_drawdown
        if pool.empty:winners[goal]=None;continue
        winner=pool.sort_values([metric,'n','config'],ascending=[False,False,True]).iloc[0]
        name=winner['config']
        winners[goal]=dict(config=name,selection=winner.to_dict(),
            later=headline[(headline.config==name)&headline.period.str.startswith('evaluation')].to_dict('records'),
            controls=paired[(paired.config==name)&paired.period.str.startswith('evaluation')].to_dict('records'))
    # Unfilled and invalid orders never disappear from opportunity counts.
    counts=[]
    e=entries[entries.control.fillna(False).eq(False)]
    for mode,g in coverage.groupby('mode'):
        filled=e[e['mode'].eq(mode)&e.filled].event_id.nunique()
        counts.append(dict(mode=mode,signals=len(g),planned=int(g.status.isin(['market','pending']).sum()),
                           filled=int(filled),fill_fraction=filled/len(g),reasons=g.status.value_counts().to_dict()))
    # First per symbol/week/side sensitivity, using signal time, before outcomes.
    sensitivity=[]
    first=signal.sort_values('signal_close').drop_duplicates(['config','symbol','week','side'])
    for name,g in first[periods(first)['all']].groupby('config'):
        sensitivity.append(dict(config=name,**stats(g)))
    pd.DataFrame(sensitivity).to_csv(EXP/'first_filled_episode_sensitivity.csv',index=False)
    summary=dict(candidates=len(candidates()),signals=int(coverage.event_id.nunique()),
        native_legs=len(legs),native_fills=sum(json.loads(p.read_text())['fills'] for p in dests),
        candidate_signal_results=len(signal),closed_results=int(signal.closed.sum()),
        censored_results=int((~signal.closed).sum()),counts=counts,winners=winners,
        mature_closed_results=int(periods(signal)['all'].sum()),
        maturity_policy='Primary ranking requires signalclose+24h pending+28d maxhold <= sourceend, uniformly across all32 candidates; pre-year selection/evaluation also purges the full29d window. all_observed retained separately.',
        baseline=headline[headline.config.eq('A_limit_5R_14d')].to_dict('records'),
        limitations=cfg['limitations'],training_eligible=False,production_eligible=False)
    # Missing/undefined metrics remain null in the portable evidence JSON.
    summary=json.loads(pd.Series([summary]).to_json(orient='values'))[0]
    dump(EXP/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,default=str),flush=True)


if __name__=='__main__':main()
