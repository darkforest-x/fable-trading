"""Reconcile every 2026 baseline ALT basket used in the combined account.

A symbol can move from 4-hour to 8-hour funding during the year. Use the
official funding interval on the actual settlement rows bracketing a position.
"""
from pathlib import Path
import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_funding as f
from yoyo.evaluation.monday_breadth_extension_pairs import EXP,PARENT


def month_archive(symbol,month):
    name=f'{symbol}-fundingRate-{month}.zip'
    sources=[f.ROOT/'funding_archives/funding'/symbol/name,Path('experiments/active/exp-monday-1618-flow-20261023-v1/inputs/funding')/symbol/name]
    file=next((p for p in sources if p.is_file()),None)
    if file is None:raise FileNotFoundError(name)
    h=pd.read_csv(file)
    h.index=pd.to_datetime(h.calc_time,unit='ms',utc=True).dt.floor('5min')
    if h.index.has_duplicates:raise ValueError(f'Duplicated official clock in {name}')
    return h.sort_index()


def interval_charge(r):
    start,end=r.decision,r.selected_exit_time
    month=start.strftime('%Y-%m')
    official=month_archive(r.symbol,month)
    lo,hi=official.index.searchsorted(start,side='left')-1,official.index.searchsorted(end,side='right')
    if start.strftime('%Y-%m')!=end.strftime('%Y-%m') or lo<0 or hi>=len(official):
        return None
    bracket=official.iloc[lo:hi+1]
    gaps=bracket.index.to_series().diff().dt.total_seconds().div(3600).iloc[1:]
    if not np.allclose(gaps,bracket.funding_interval_hours.iloc[1:],rtol=0,atol=1e-6):
        return None
    observed=f.rates(r.symbol,months=(month,),local_only=True).loc[start:end]
    settled=official.loc[start:end]
    if not observed.index.equals(settled.index):raise ValueError('Archived clocks disagree')
    np.testing.assert_allclose(observed.realized_rate,settled.last_funding_rate)
    cost=observed.realized_rate*observed.mark_price
    if cost.isna().any():raise ValueError('Unobserved funding mark')
    boundary=cost.index.isin([start,end])
    cost.loc[boundary]=cost.loc[boundary].clip(lower=0)
    charged=100*cost.sum()/r.entry_open_6h
    return dict(symbol=r.symbol,decision=start,exit_time=end,official_settlements=len(settled),
                current_interval_hours=bracket.funding_interval_hours.iloc[-1],funding_cost_pct=charged)


def run():
    d=pd.read_parquet(EXP/'alt/member_strategy_returns.parquet').query('epoch==3 and strategy=="baseline_6h"').copy()
    # Preserve the exact thirteen event/member pairs flagged by the original
    # cross-month cadence check, even after the ALT builder learned event-day cadence.
    audit_path=EXP/'risk/alt_6h_official_interval_checks.csv'
    candidates=d.selected_funded_net_pct.isna()
    if audit_path.is_file():
        flagged=pd.read_csv(audit_path,parse_dates=['decision'])[['symbol','decision']]
        candidates |= pd.MultiIndex.from_frame(d[['symbol','decision']]).isin(pd.MultiIndex.from_frame(flagged))
    repaired=[]
    for i,r in d[candidates].iterrows():
        proof=interval_charge(r)
        if proof is None:raise ValueError(f'Unresolved baseline funding for {r.symbol}')
        d.at[i,'selected_funded_net_pct']=r.selected_net_pct-proof['funding_cost_pct']
        repaired.append(proof)
    if d.selected_funded_net_pct.isna().any():raise ValueError('Missing member outcome')
    check=d.groupby('decision',as_index=False).agg(verified_funded_pct=('selected_funded_net_pct','mean'),members=('symbol','size'))
    ref=pd.read_parquet(PARENT/'audit/fixed/all_rule_and_account_events.parquet').query('epoch==3 and candidate_id=="freq36_ALT"')
    check=check.merge(ref[['decision','funded_pct']],on='decision',validate='one_to_one')
    assert len(check)==25
    np.testing.assert_allclose(check.verified_funded_pct,check.funded_pct,atol=1e-6)
    check.to_csv(EXP/'risk/alt_6h_funding_reconciliation.csv',index=False)
    if repaired:pd.DataFrame(repaired).to_csv(audit_path,index=False)
    members=pd.read_parquet(EXP/'alt/member_strategy_returns.parquet').query('epoch==3').copy()
    for _,group in members[members.selected_funded_net_pct.isna()].groupby(['symbol','decision','selected_exit_time']):
        try:
            proof=interval_charge(group.iloc[0])
        except FileNotFoundError:
            proof=None
        if proof is not None:
            members.loc[group.index,'selected_funded_net_pct']=group.selected_net_pct-proof['funding_cost_pct']
    scores=members.groupby(['strategy','decision']).selected_funded_net_pct.agg(['count','size','mean'])
    scores['funded_net_pct']=scores['mean'].where(scores['count'].eq(scores['size']))
    scores.drop(columns='mean').to_csv(EXP/'risk/alt_policy_verified_events_2026.csv')


if __name__=='__main__':
    run()
