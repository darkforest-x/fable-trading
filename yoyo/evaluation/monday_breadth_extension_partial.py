"""Reallocate only rare-case BTC exits on the same non-overlapping dual-source clock.

Reuse the funded VectorBT fills for a half-sale at the funded dollar target and
half retained to six days, either with the initial protection or a 4% trailing stop.
"""
import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation.monday_breadth_extension_pairs import EXP, PARENT
from yoyo.evaluation.monday_breadth_opt_match_exits import paired_month_p

OUT=EXP/'partial'
FIELDS=['entry_open','net_pct','funded_pct','funding_points','net_points','funded_points','exit_time','exit_legs','first_exit_time']


def run():
    OUT.mkdir(exist_ok=True)
    book=pd.read_parquet(EXP/'dual/events.parquet').query("strategy=='dual_source_specific'").copy()
    base=pd.read_parquet(PARENT/'audit/fixed/all_rule_and_account_events.parquet')
    alt=base[base.candidate_id.eq('freq36_ALT') & base.epoch.eq(3)]
    cases=book[book.source.eq('case')]
    stored=pd.read_parquet(funding.FUNDED_CACHE,filters=[('policy_id','in',[0,4,5])])
    ctrl=pd.read_csv(EXP/'dual/matched_2026.csv',parse_dates=['decision']).query("strategy=='dual_source_specific'").set_index('decision')
    matched_case=pd.read_parquet(PARENT/'btc_target/matched_draws_2026.parquet')
    control_cases=matched_case[matched_case.decision.isin(cases[cases.epoch.eq(3)].decision)]
    data=[];results=[];control=[]
    for policy_id,name in [(0,'target_all'),(4,'half_then_6d'),(5,'half_then_trail4')]:
        native=stored[stored.policy_id.eq(policy_id)].set_index('entry_time')
        rows=book.copy().set_index('decision')
        if not cases.decision.isin(native.index).all():
            raise ValueError('The original native funded partial-fill cache is incomplete')
        for field in FIELDS:
            rows.loc[cases.decision,field]=native.loc[cases.decision,field].to_numpy()
        rows=rows.reset_index()
        rows['exit_policy']=name
        data.append(rows[['decision','epoch','source','exit_policy',*FIELDS]])
        if policy_id==0:np.testing.assert_allclose(rows.funded_points,book.funded_points,atol=1e-7)
        test=rows[rows.epoch.eq(3)]
        c=ctrl[['actual','random']].copy()
        if len(control_cases):
            rr=native.loc[control_cases.random_entry].funded_pct.to_numpy()
            averaged=control_cases.assign(random=rr).groupby('decision').random.mean()
            c.loc[averaged.index,'actual']=test.set_index('decision').loc[averaged.index,'funded_pct']
            c.loc[averaged.index,'random']=averaged
        edge=c.actual-c.random
        control.append(dict(exit_policy=name,pairs=len(c),covered_case_clocks=len(control_cases.decision.unique()),
                            edge=edge.mean(),monthly_p=paired_month_p(edge)))
        for period,g in [('all',rows),*[(str(k),v) for k,v in rows.groupby('epoch')]]:
            growth=(1+g.funded_pct/100).prod()
            recent=period=='3'
            sept=g[g.decision.between('2026-09-15','2026-09-16')]
            cases_here=g[g.source.eq('case')]
            results.append(dict(exit_policy=name,period=period,n=len(g),funded_mean=g.funded_pct.mean(),
                                funded_win=100*g.funded_pct.gt(0).mean(),btc_funded_growth=100*(growth-1),
                                case_signals=len(cases_here),case_partial_sales=cases_here.exit_legs.eq(2).sum(),
                                equal_sleeves_growth=100*((growth+(1+alt.funded_pct/100).prod())/2-1) if recent else np.nan,
                                partial_sept_net_points=sept.funded_points.iloc[0] if recent else np.nan))
    pd.concat(data,ignore_index=True).to_parquet(OUT/'events.parquet',index=False)
    pd.DataFrame(results).to_csv(OUT/'periods.csv',index=False)
    pd.DataFrame(control).to_csv(OUT/'controls.csv',index=False)


if __name__=='__main__':
    run()
