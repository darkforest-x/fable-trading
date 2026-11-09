"""Size funded parents on the corrected roster; resolve margin ambiguities against native minutes."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_margin_before_stop as evidence
from yoyo.evaluation import cloud_close_notice_tail_risk as source

base = evidence.base
EXP = source.EXP.parent/'exp-cloud-close-verified-exposure-20261109-v1'


def account():
    rows,quotes,_ = source.inputs()
    data,_ = base.parent.assemble(rows,source.correction.parent.old.changed_limits(rows,quotes),'OKX',quotes)
    return data,base.underlying(data,quotes),quotes


def simulate(data,market,quotes,multiples,slip,directory,name,*,reference_dir=None,snapshot=None,inspect_flag=evidence.inspect):
    native,previous,side,runners = market
    if set(multiples.index)!=set(runners.event_id) or not multiples.index.is_unique or multiples.isna().any():
        raise ValueError('Each occupied parent needs exactly one pre-entry multiplier')
    assigned = runners.event_id.map(multiples)
    audits = {}
    for ratio in sorted(multiples.unique()):
        group = runners[assigned.eq(ratio)].reset_index(drop=True)
        raw,summary = base.study.leverage._margin_audit(group,ratio,slip,quote_loader=quotes)
        details = []
        for r in raw[raw.maintenance_boundary_hit].itertuples():
            if inspect_flag is None:
                details.append(dict(event_id=r.event_id,symbol=r.symbol,status='native_margin_order_unresolved'))
            else:
                details.extend(inspect_flag(r,ratio,slip,quotes))
        summary['checked_hours'] = details
        summary['all_flags_resolved'] = all(x['status']=='stop_strictly_earlier' for x in details)
        audits[str(ratio)] = summary
    if not all(v['all_flags_resolved'] for v in audits.values()):
        return dict(margin=audits,unresolved=True)
    model = dict(data,actions=data['actions'].copy())
    for ratio in sorted(multiples.unique()):
        model['selected'] = data['selected'][data['selected'].event_id.map(multiples).eq(ratio)]
        model,_ = base.study.leverage._syntheticize(model,ratio,slip)
    model['selected'] = data['selected']
    ratio_grid = np.zeros_like(native)
    for row in runners.itertuples():
        first,last = (np.array([row.entry_ms,row.exit_bar_ms])-data['start'])//base.wallet.HOUR
        ratio_grid[first:last+1,row.slot//4] = multiples.at[row.event_id]
    if (ratio_grid[native>0]<=0).any():
        raise ValueError('Native units lost their collateral rate')
    pf = base.build_portfolio(data,model,native*ratio_grid,previous*ratio_grid,side,1.,slip)
    funds,curve,trims = base.receipts(pf,data,model,multiples)
    funds['exposure'] = multiples
    if snapshot is not None:
        snapshot(pf,data,funds,curve,multiples,slip)
    elif reference_dir is not None and (multiples==1.5).all():
        suffix = '_stress' if slip else ''
        old = pd.read_csv(reference_dir/f'hourly_1.5x{suffix}.csv.gz',index_col=0)
        old_trades = pd.read_csv(reference_dir/f'funded_1.5x{suffix}.csv.gz',index_col='event_id')
        if not np.allclose(curve[old.columns],old,atol=.0001,rtol=1.e-9) or not np.allclose(
                funds.loc[old_trades.index,'cash_pnl'],old_trades.cash_pnl,atol=.0001,rtol=1.e-9):
            raise ValueError('Conditional adapter does not reproduce the corrected native reference')
    else:
        funds.to_csv(directory/f'funded_{name}.csv.gz')
        curve.to_csv(directory/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
    return dict(metrics=base.parent.spot.hourly.tested.summarize(funds,curve),margin=audits,trim_fills=trims)


def run(directory,policies,*,account_loader=account,reference_dir=source.BASE):
    if (directory/'summary.json').exists():
        raise FileExistsError('Completed native exposure study')
    data,market,quotes = account_loader()
    parents = market[-1].set_index('event_id')
    out = {}
    for key,weights in policies(parents).items():
        for slip in ([0.] if key=='reference' else [0.,.0005]):
            label = key+('_stress' if slip else '')
            out[label] = simulate(data,market,quotes,weights,slip,directory,label,reference_dir=reference_dir)
            print(label,out[label].get('metrics',{}).get('all'),flush=True)
            gc.collect()
    source.correction.archive.prices.safe_json(directory/'summary.json',dict(arms=out,
            limitations='Price-based margin approximation with 10% MMR, original CVC gap, omitted funding and capacity.'))


def fixed(parents):
    return {k:pd.Series(v,index=parents.index) for k,v in [('reference',1.5),('uniform_1.75',1.75),('uniform_2',2.)]}


if __name__=='__main__':
    run(EXP,fixed)
