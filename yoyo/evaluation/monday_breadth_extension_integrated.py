"""Chronological event counts and controls for the single integrated reversal candidate."""
import numpy as np
import pandas as pd

from yoyo.evaluation.monday_breadth_extension_pairs import EXP,PARENT
from yoyo.evaluation.monday_breadth_opt_match_exits import paired_month_p


def growth(group):
    return 100*(group.groupby('asset').result.agg(lambda x:(1+x/100).prod()).mean()-1)


def run():
    out=EXP/'risk'
    original=pd.read_parquet(PARENT/'audit/fixed/all_rule_and_account_events.parquet')
    alt=original[original.candidate_id.eq('freq36_ALT')].assign(source='ALT')
    b=pd.read_parquet(out/'integrated_btc_events.parquet').assign(asset='BTC')
    book=pd.concat([b,alt],ignore_index=True)
    y=book[book.epoch.eq(3)].copy()
    y['result']=y.funded_pct
    if not y.result.notna().all():raise ValueError('Incomplete 2026 funding')
    months=y.decision.dt.strftime('%Y-%m')
    monthstats=y.assign(month=months).groupby('month').agg(distinct=('decision','nunique'),legs=('asset','size'),wins=('result',lambda x:(x>0).sum()))
    monthstats.to_csv(out/'integrated_months.csv')
    years=[]
    for year,g in book.groupby(book.decision.dt.year):
        years.append(dict(year=year,distinct=g.decision.nunique(),BTC=g.asset.eq('BTC').sum(),ALT=g.asset.eq('ALT').sum(),
                          growth_fee_only_year_reset= growth(g.assign(result=g.net_pct))))
    pd.DataFrame(years).to_csv(out/'integrated_years.csv',index=False)
    masks={'all_2026':pd.Series(True,index=y.index),
           'omit_september_entries':~months.eq('2026-09'),
           'omit_owner_case_entry':~y.decision.eq(pd.Timestamp('2026-09-15 19:20',tz='UTC')),
           'omit_best_trade_each_sleeve':~y.index.isin(y.groupby('asset').result.idxmax())}
    for m in sorted(months.unique()):masks['omit_'+m]=months.ne(m)
    feats=[]
    for label,mask in masks.items():
        g=y[mask]
        feats.append(dict(subset=label,distinct=g.decision.nunique(),btc=g.asset.eq('BTC').sum(),alt=g.asset.eq('ALT').sum(),
                          wins=int(g.result.gt(0).sum()),win_pct=100*g.result.gt(0).mean(),growth_funded=growth(g),
                          min_month=monthstats.distinct.min(),max_month=monthstats.distinct.max()))
    pd.DataFrame(feats).to_csv(out/'integrated_sensitivity.csv',index=False)
    f=pd.read_csv(EXP/'btc/below/rule_summary.csv').query('priority_rank_within_frequency_eligible==1').rule.item()
    bctrl=pd.read_csv(EXP/'btc/below/control_events_2026.csv',parse_dates=['decision'])
    bctrl=bctrl[bctrl.rule.eq(f)&bctrl.decision.isin(y[y.source.eq('medium')].decision)].dropna(subset=['random_net_mean_pct'])
    bctrl=bctrl.rename(columns={'net_pct':'real','random_net_mean_pct':'control'}).assign(asset='BTC')
    actrl=pd.read_parquet(PARENT/'controls/fixed_paired_events.parquet').query("candidate_id=='freq36_ALT' and eligible_coverage")
    actrl=actrl.rename(columns={'matched_actual_pct':'real','matched_random_pct':'control'})
    p=pd.concat([bctrl[['asset','decision','real','control']],actrl[['asset','decision','real','control']]],ignore_index=True)
    if p.duplicated(['asset','decision']).any():raise ValueError('Duplicate matched event')
    p['weight']=.5/p.groupby('asset').real.transform('size')
    p['weighted_edge']=(p.real-p.control)*p.weight
    p.to_csv(out/'integrated_control_events.csv',index=False)
    alt_matched=actrl.decision.nunique()
    res=dict(total=len(y),btc_actual=y.asset.eq('BTC').sum(),matched_btc=len(bctrl),alt_actual=y.asset.eq('ALT').sum(),
             matched_alt=alt_matched,case_matched=0,matched=len(p),actual=(p.real*p.weight).sum(),random=(p.control*p.weight).sum(),
             edge_pp=p.weighted_edge.sum(),matched_months=p.decision.dt.strftime('%Y-%m').nunique(),
             p_month=paired_month_p(p.set_index('decision').weighted_edge))
    case_draws=pd.read_parquet(PARENT/'btc_target/matched_draws_2026.parquet')
    if case_draws.decision.isin(y[y.source.eq('case')].decision).any():
        raise ValueError('Reapply the same partial exit to the new case controls')
    pd.DataFrame([res]).to_csv(out/'integrated_controls.csv',index=False)


if __name__=='__main__':
    run()
