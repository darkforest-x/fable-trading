"""Admit new 12H cloud positions only in a profitable trailing shadow-portfolio regime."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_exit_rotation as rotation
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_volatility_expansion as market

EXP = rotation.EXP.parent/'exp-cloud-close-shadow-trend-20261109-v1'


def cohorts(venue,label,paths):
    """Both 90-day NAV marks predate the executable signal, with a 12H lag from the most recent mark."""
    cols = ['event_id','entry_ms','selection_evidence_valid','partial_return_90d','partial_nav_start','partial_nav_end','lookback_start_ms','lookback_end_ms']
    data = pd.read_csv(rotation.EXP/f'choice_evidence_{venue}_{label}.csv.gz',usecols=cols)
    roster = paths.loc[paths.tp_r.eq(0)].set_index('event_id').entry_ms.sort_index()
    if not roster.equals(data.set_index('event_id').entry_ms.sort_index()):
        raise ValueError('Shadow observations and original decision records differ')
    seen = data[data.selection_evidence_valid]
    if not seen.lookback_end_ms.eq(seen.entry_ms-rotation.LOOKBACK_LAG_MS).all():
        raise ValueError('Shadow equity observations fall after the pre-key boundary')
    if not (seen.lookback_end_ms-seen.lookback_start_ms).eq(90*24*rotation.HOUR_MS).all():
        raise ValueError('The two valuation endpoints do not cover the fixed 90-day interval')
    if not np.allclose(seen.partial_return_90d,seen.partial_nav_end/seen.partial_nav_start-1,atol=1e-12):
        raise ValueError('Shadow returns do not reconcile to observed funded NAV')
    passing = seen.loc[seen.partial_return_90d.gt(0),'event_id']
    return paths[paths.event_id.isin(seen.event_id)],paths[paths.event_id.isin(passing)]


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Frozen shadow-return filter already evaluated')
    baseline = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    rows = baseline[baseline.scenario.eq('actual')].assign(policy='unfiltered').to_dict('records')
    summary = {}
    for venue in ['OKX','Binance']:
        _,actual,control = rotation._load_paths(venue)
        warm,real = cohorts(venue,'actual',actual)
        _,control = cohorts(venue,'control',control)
        paired = real[real.parent_id.isin(control.parent_id)]
        control = control[control.parent_id.isin(paired.parent_id)]
        summary[venue] = dict(covered=len(warm)//2,positive=len(real)//2,matched=len(paired)//2)
        for tag,sample,slip in [('coverage',warm,0.),('actual',real,0.),('paired',paired,0.),('control',control,0.),('stress',real,.0005)]:
            metrics,pos,eq = market.fund(venue,sample,slip)
            rows.extend(dict(venue=venue,policy='positive_shadow',scenario=tag,period=p,**m) for p,m in metrics.items())
            if tag=='actual':
                summary[venue].update(dev_pass=second.meets(metrics['dev']),full_pass=second.meets(metrics['all']))
                pos.to_csv(EXP/f'funded_{venue}.csv.gz')
                eq.to_csv(EXP/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
            del pos,eq
            gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')][['venue','policy','scenario','final_usdt','win_pct','payoff_usdt','dd_bound_pct']].to_string(index=False))


if __name__=='__main__':
    main()
