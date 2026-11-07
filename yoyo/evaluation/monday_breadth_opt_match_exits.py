"""Reprice matched controls using the identical finalist policy through the same VectorBT ledger."""
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation.spike_market_breadth_matched_controls import paired_sign_flip_p

CONTROL=study.search.OUT/'controls'


def paired_month_p(differences):
    cents=differences.dropna()
    sums=cents.groupby(cents.index.strftime('%Y-%m')).sum().to_numpy()
    if len(sums)<2:return np.nan
    if len(sums)>16:return paired_sign_flip_p(pd.Series(sums),seed=20261108,draws=65535)
    signs=1-2*((np.arange(2**len(sums))[:,None]>>np.arange(len(sums)))&1)
    return np.mean(np.abs(signs@sums)>=abs(sums.sum())-1e-10)


def adjust_primary(results,role):
    part=results[results.role==role].copy().sort_values('p_month')
    # Include all four intended assets, even if one has no testable pairs.
    vals=part.p_month.to_numpy()
    adjusted=np.maximum.accumulate(np.where(np.isfinite(vals),(4-np.arange(len(vals)))*vals,1)).clip(0,1)
    results.loc[part.index,'p_holm']=np.where(np.isfinite(vals),adjusted,np.nan)


def study_dynamic():
    inp=CONTROL/'dynamic_matches.parquet'
    matches=pd.read_parquet(inp)
    rules=pd.read_csv(study.OUT/'locked_rules.csv')
    events=pd.read_parquet(study.OUT/'locked_events.parquet')
    matches=matches.dropna(subset=['random_entry']).copy()
    matches=matches[matches.epoch==3]
    for k in ('decision','random_entry'):matches[k]=pd.to_datetime(matches[k],utc=True)
    if 'policy_id' not in matches:matches=matches.merge(rules[['candidate_id','policy_id']],on='candidate_id',validate='many_to_one')
    meets=matches[['symbol','decision','random_entry','policy_id']].drop_duplicates()
    jobs=[(sym,list(pd.Index(sub.decision).union(sub.random_entry).unique()),sorted(sub.policy_id.unique()))
          for sym,sub in meets[meets.policy_id<1000].groupby('symbol')]
    with ProcessPoolExecutor(2) as pool:
        for i,(sym,n) in enumerate(pool.map(study.fill_symbol,jobs),1):
            if i%50==0:print('random exit fills',i,'/',len(jobs),flush=True)
    funded=meets[meets.policy_id>=1000]
    if len(funded):
        from yoyo.evaluation import monday_breadth_opt_funding as funding
        extended=funding.existing_or_simulate(pd.Index(funded.decision).union(funded.random_entry).unique())
        extended=extended[['entry_time','policy_id','funded_pct']].rename(columns={'funded_pct':'net_pct'})
        extended['policy_id']+=1000
    paired=[]
    for sym,g in matches.groupby('symbol'):
        path=study.CACHE/(sym+'.parquet')
        if not path.is_file():continue
        f=pd.read_parquet(path,columns=['entry_time','policy_id','net_pct'])
        if sym=='BTCUSDT' and len(funded):
            f=pd.concat([f,extended],ignore_index=True)
        sub=g.merge(f.rename(columns={'entry_time':'random_entry','net_pct':'random_pct'}),on=['random_entry','policy_id'],how='left',validate='many_to_one')
        sub=sub.merge(f.rename(columns={'entry_time':'decision','net_pct':'actual_pct'}),on=['decision','policy_id'],how='left',validate='many_to_one')
        paired.append(sub)
    if not paired:
        pd.DataFrame(columns=['candidate_id','decision','actual_pct','random_pct','coverage']).to_parquet(CONTROL/'dynamic_paired_events.parquet',index=False)
        return
    obs=pd.concat(paired,ignore_index=True)
    obs.to_parquet(CONTROL/'dynamic_individual_payoffs.parquet',index=False)
    # Equalize draw count per sampled constituent; match actual and control on identical currencies.
    valid=obs.dropna(subset=['actual_pct','random_pct']).groupby(['candidate_id','decision','symbol']).agg(
         weight=('weight','first'),actual_pct=('actual_pct','first'),random_pct=('random_pct','mean'),draws=('draw','nunique')).reset_index()
    valid['actual']=valid.actual_pct*valid.weight
    valid['random']=valid.random_pct*valid.weight
    weighted=valid.groupby(['candidate_id','decision']).agg(actual=('actual','sum'),random=('random','sum'),
          total_weight=('weight','sum'),n_members=('symbol','size'),min_draws=('draws','min')).reset_index()
    weighted['actual_pct']=weighted.actual/weighted.total_weight
    weighted['random_pct']=weighted.random/weighted.total_weight
    paired=weighted.merge(events[['candidate_id','decision','expected','net_pct','epoch']],on=['candidate_id','decision'],validate='one_to_one')
    paired['sampled_member_share']=paired.n_members/paired.expected
    paired.to_parquet(CONTROL/'dynamic_paired_events.parquet',index=False)


def evaluate(rules,events,pairs,primary):
    results=[]
    for row in rules.itertuples():
        if row.role not in (primary,'retention','case_target','case_runner','baseline'):continue
        e=events[(events.candidate_id==row.candidate_id)&(events.epoch==3)].dropna(subset=['net_pct'])
        group=pairs[pairs.candidate_id==row.candidate_id].dropna(subset=['actual_pct','random_pct']).copy()
        group=group[group.decision.isin(e.decision)].set_index('decision')
        diff=group.actual_pct-group.random_pct
        stat=study.search.describe(e)
        results.append(dict(candidate_id=row.candidate_id,role=row.role,asset=row.asset,
            n_all=len(e),n_pairs=len(diff),coverage=100*len(diff)/len(e) if len(e) else 0,
            months=diff.index.strftime('%Y-%m').nunique() if len(diff) else 0,
            all_net=stat['mean'],all_ex_best_month=stat['exmonth'],all_stress=stat['stress'],
            paired_actual=group.actual_pct.mean(),paired_random=group.random_pct.mean(),paired_edge=diff.mean(),
            p_month=paired_month_p(diff) if len(diff) else np.nan))
    out=pd.DataFrame(results)
    out['p_holm']=np.nan
    adjust_primary(out,primary)
    out['pass']=((out.n_pairs>=10)&(out.months>=6)&(out.coverage>=50)&(out.p_holm<.01)&(out.paired_edge>0)
                 &(out.all_ex_best_month>0)&(out.all_stress>0)&out.role.eq(primary))
    return out


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['dynamic','stats_dynamic'])
    mode=p.parse_args().stage
    if mode=='dynamic':study_dynamic()
    else:
        ev=pd.read_parquet(study.OUT/'locked_events.parquet')
        pairs=pd.read_parquet(CONTROL/'dynamic_paired_events.parquet')
        if not pairs.empty:
            pairs=pairs[pairs.sampled_member_share>=.80]
        final=evaluate(pd.read_csv(study.OUT/'locked_rules.csv'),ev,pairs,'dynamic_primary')
        final.to_csv(CONTROL/'dynamic_validity.csv',index=False)
