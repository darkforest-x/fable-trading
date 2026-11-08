"""Cancel the original 2R take-profit after one completed 12H interval."""
import argparse
import gc
import json
from contextlib import nullcontext

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_archive_cohort as archive
from yoyo.evaluation import cloud_close_efficient_trend as trend

EXP=archive.EXP.parent/'exp-cloud-close-target-expiry-20261109-v1'


def time_in_force(paths, *, defer=False, hours=12):
    """By the next 12H boundary all previous H1 fills are known; cancel any unfilled limit."""
    if hours<=0 or hours%12:
        raise ValueError('Resting limit expires at a completed 12h boundary')
    target=paths[paths.tp_r.eq(2)].copy()
    is_limit=target.exit_reason.str.startswith('take_profit')
    if not np.array_equal(is_limit,target.exit_fee_rate.lt(.0005)):
        raise ValueError('Native TP path and fee class disagree')
    if (target.entry_ms % (12*archive.native.wallet.HOUR)).any():
        raise ValueError('Native entry not aligned to a 12h boundary')
    target['expiry_ms']=target.entry_ms+hours*archive.native.wallet.HOUR
    early = target.exit_bar_ms.lt(target.expiry_ms)
    replace_limit = is_limit & (early if defer else ~early)
    target['choice']=np.where(replace_limit,'full_runner','partial_2_3')
    return trend.rotation._rotated_paths(paths,target),target


def main(defer=False):
    directory = EXP.parent/'exp-cloud-close-deferred-target-20261109-v1' if defer else EXP
    policy = 'deferred_target' if defer else 'cancel_after_12h'
    if (directory/'summary.json').exists():
        raise FileExistsError('Completed resting-order expiry experiment')
    extra=set(json.loads((archive.EXP/'summary.json').read_text())['archived_additions'])
    output={}; records=[]
    for venue in ['OKX','Binance']:
        if venue=='OKX':
            paths=trend.thirds.okx_paths()
            trend.repair.check_parent_sets(paths)
            reference=pd.read_csv(trend.repair.EXP/'thirds/account_metrics.csv')
            base=reference[reference.venue.eq('OKX') & reference.scenario.eq('actual')]
        else:
            paths=archive.pool(extra)
            reference=pd.read_csv(archive.EXP/'account_metrics.csv')
            base=reference[reference.policy.eq('normal_partial') & reference.scenario.eq('actual')]
        records.extend(base.assign(policy='normal_partial',venue=venue).to_dict('records'))
        selected,targets=time_in_force(paths,defer=defer)
        real=selected[~selected.is_control]
        control=selected[selected.is_control & selected.parent_id.isin(real.parent_id)]
        with (archive.market_quotes(extra) if venue=='Binance' else nullcontext()):
            metrics,trades,eq=trend.market.fund(venue,real,0.)
            trades.to_csv(directory/f'funded_{venue}.csv.gz')
            eq.to_csv(directory/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
            eligible=targets[~targets.is_control]
            funded=eligible[eligible.event_id.isin(trades.index)]
            baseline_path=archive.EXP/'funded_normal_partial.csv.gz' if venue=='Binance' else trend.repair.EXP/'thirds/funded_OKX.csv.gz'
            frozen_ids=pd.read_csv(baseline_path,usecols=['event_id']).event_id
            if set(frozen_ids)!=set(trades.index):
                raise ValueError('Partial limit expiry changed native runner admission roster')
            output[venue]=dict(dev_pass=trend.second.meets(metrics['dev']),full_pass=trend.second.meets(metrics['all']),
                               full=metrics['all'],dev=metrics['dev'],**{('early_limits_waived' if defer else 'late_limits_funded'):int(funded.choice.eq('full_runner').sum())})
            records.extend(dict(policy=policy,venue=venue,scenario='actual',period=p,**m) for p,m in metrics.items())
            if output[venue]['dev_pass']:
                for name,part,slip in [('paired',real[real.parent_id.isin(control.parent_id)],0.),('control',control,0.),('stress',real,.0005)]:
                    more=trend.market.fund(venue,part,slip)[0]
                    records.extend(dict(policy=policy,venue=venue,scenario=name,period=p,**m) for p,m in more.items())
        del paths,selected,real,control,trades,eq
        gc.collect()
    pd.DataFrame(records).to_csv(directory/'account_metrics.csv',index=False)
    (directory/'summary.json').write_text(json.dumps(output,indent=2,allow_nan=False)+'\n')
    print(json.dumps(output,indent=2,allow_nan=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--defer',action='store_true')
    main(parser.parse_args().defer)
