"""Use the same native cash engine with separately fixed long and short leverage."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_waves_minute_risk as historical

repair = historical.repair
base = repair.old.exposure
EXP = repair.EXP.parent/'exp-cloud-close-directional-exposure-20261109-v1'


def simulate(data,market,quotes,long_mult,short_mult,slip):
    native,previous,side,runners = market
    model = dict(data,actions=data['actions'].copy())
    checks = {}
    multiples = pd.Series(np.where(runners.side.gt(0),long_mult,short_mult),index=runners.event_id)
    for direction,ratio in [(1,long_mult),(-1,short_mult)]:
        chosen = runners[runners.side.eq(direction)]
        table,checks[str(direction)] = base.study.leverage._margin_audit(chosen.reset_index(drop=True),ratio,slip,quote_loader=quotes)
        checks[str(direction)]['events_at_boundary'] = table.loc[table.maintenance_boundary_hit,'event_id'].tolist()
    if not all(v['valid'] for v in checks.values()):
        return dict(margin=checks,valid=False)
    # Source slots have disjoint active time windows, so transform only the
    # matching side's native fills/marks on each pass. No synthetic idle cash.
    for direction,ratio in [(1,long_mult),(-1,short_mult)]:
        model['selected'] = data['selected'][data['selected'].side.eq(direction)]
        model,_ = base.study.leverage._syntheticize(model,ratio,slip)
    model['selected'] = data['selected']
    ratio_grid = np.where(side>0,long_mult,short_mult)
    pf = base.build_portfolio(data,model,native*ratio_grid,previous*ratio_grid,side,1.,slip)
    funds,curve,trims = base.receipts(pf,data,model,multiples)
    name = f'long{long_mult:g}_short{short_mult:g}'+('_stress' if slip else '')
    if long_mult==short_mult==1.5 and not slip:
        prev = pd.read_csv(repair.EXP/'hourly_1.5x.csv.gz',index_col=0)
        prev_funds = pd.read_csv(repair.EXP/'funded_1.5x.csv.gz',index_col='event_id')
        if (not np.allclose(curve[prev.columns],prev,rtol=1e-8,atol=.005) or
                not np.allclose(funds.loc[prev_funds.index,'cash_pnl'],prev_funds.cash_pnl,rtol=1e-8,atol=.005)):
            raise ValueError('Directional adapter does not reproduce the original full 1.5x account')
    else:
        curve.to_csv(EXP/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
        funds.to_csv(EXP/f'funded_{name}.csv.gz')
    return dict(metrics=base.parent.spot.hourly.tested.summarize(funds,curve),margin=checks,
                trim_fills=trims,valid=True)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed long-side leverage study')
    rows,quotes,_ = historical.inputs()
    data,_ = base.parent.assemble(rows,repair.old.changed_limits(rows,quotes),'OKX',quotes)
    market = base.underlying(data,quotes)
    out = {}
    for long_mult in [1.5,2.,3.]:
        for slip in ([0.] if long_mult==1.5 else [0.,.0005]):
            key = f'long_{long_mult:g}'+('_stress' if slip else '')
            out[key] = simulate(data,market,quotes,long_mult,1.5,slip)
            print(key,out[key].get('metrics',{}).get('all'),out[key]['valid'],flush=True)
            gc.collect()
    repair.archive.prices.safe_json(EXP/'summary.json',dict(arms=out,short_multiple=1.5))


if __name__=='__main__':
    main()
