"""Resolve drawdown for the OKX trend-efficiency account with its actual funded exit choices."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binding_risk_validation as refinement
from yoyo.evaluation import cloud_close_efficient_trend as study

EXP = study.EXP


def main():
    target = EXP/'okx_refinement.json'
    if target.exists():
        raise FileExistsError('Funded OKX valuation already refined')
    eq = pd.read_csv(EXP/'hourly_OKX.csv.gz',index_col='hour_end_utc',parse_dates=True)
    funded = pd.read_csv(EXP/'funded_OKX.csv.gz',usecols=['event_id','notional','cash_pnl'])
    choices = pd.read_csv(EXP/'decisions_OKX.csv.gz',usecols=['event_id','choice'])
    choices = choices[choices.event_id.isin(funded.event_id)]
    paths = study.thirds.okx_paths()
    legs = study.rotation._rotated_paths(paths[paths.event_id.isin(funded.event_id)],choices)
    h1_ambiguous = eq.index[eq.lower_nav.div(eq.upper_nav.cummax().clip(lower=1000)).lt(.65)]
    result = refinement.refine_funded_okx(eq,funded,legs,EXP/'okx_refined_marks.csv')
    result['hours_exceeding_35_before_refinement'] = [i.isoformat() for i in h1_ambiguous]
    dev = eq.loc[eq.index<=pd.Timestamp('2025-01-01T00:00Z')]
    result['dev_upper_pct'] = refinement.dd.max_drawdown(dev)
    marks = np.r_[1000.,dev[['open_nav','open_exit_nav','nav']].to_numpy().ravel()]
    result['dev_observed_pct'] = float(100*(1-(marks/np.maximum.accumulate(marks)).min()))
    metrics = pd.read_csv(EXP/'account_metrics.csv')
    account = metrics[metrics.venue.eq('OKX') & metrics.policy.eq('trend_efficiency') & metrics.scenario.eq('actual')].set_index('period')
    for period,cap in [('all',result['refined_upper_pct']),('dev',result['dev_upper_pct'])]:
        row = account.loc[period].copy()
        row['dd_bound_pct'] = cap
        result[period+'_pass'] = study.second.meets(row)
    target.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
