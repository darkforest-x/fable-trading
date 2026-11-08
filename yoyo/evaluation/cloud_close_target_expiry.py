"""Cancel the original 2R take-profit after one completed 12H interval."""
import gc
import json
from contextlib import nullcontext

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_archive_cohort as archive
from yoyo.evaluation import cloud_close_efficient_trend as trend

EXP=archive.EXP.parent/'exp-cloud-close-target-expiry-20261109-v1'


def time_in_force(paths):
    """By the next 12H boundary all previous H1 fills are known; cancel any unfilled limit."""
    target=paths[paths.tp_r.eq(2)].copy()
    is_limit=target.exit_reason.str.startswith('take_profit')
    if not np.array_equal(is_limit,target.exit_fee_rate.lt(.0005)):
        raise ValueError('Native TP path and fee class disagree')
    if (target.entry_ms % (12*archive.native.wallet.HOUR)).any():
        raise ValueError('Native entry not aligned to a 12h boundary')
    target['expiry_ms']=target.entry_ms+12*archive.native.wallet.HOUR
    late=is_limit & target.exit_bar_ms.ge(target.expiry_ms)
    target['choice']=np.where(late,'full_runner','partial_2_3')
    return trend.rotation._rotated_paths(paths,target),target


def main():
    if (EXP/'summary.json').exists():
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
        selected,targets=time_in_force(paths)
        real=selected[~selected.is_control]
        control=selected[selected.is_control & selected.parent_id.isin(real.parent_id)]
        with (archive.market_quotes(extra) if venue=='Binance' else nullcontext()):
            metrics,trades,eq=trend.market.fund(venue,real,0.)
            trades.to_csv(EXP/f'funded_{venue}.csv.gz')
            eq.to_csv(EXP/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
            eligible=targets[~targets.is_control]
            funded=eligible[eligible.event_id.isin(trades.index)]
            baseline_path=archive.EXP/'funded_normal_partial.csv.gz' if venue=='Binance' else trend.repair.EXP/'thirds/funded_OKX.csv.gz'
            frozen_ids=pd.read_csv(baseline_path,usecols=['event_id']).event_id
            if set(frozen_ids)!=set(trades.index):
                raise ValueError('Partial limit expiry changed native runner admission roster')
            output[venue]=dict(dev_pass=trend.second.meets(metrics['dev']),full_pass=trend.second.meets(metrics['all']),
                               full=metrics['all'],dev=metrics['dev'],late_limits_funded=int(funded.choice.eq('full_runner').sum()))
            records.extend(dict(policy='cancel_after_12h',venue=venue,scenario='actual',period=p,**m) for p,m in metrics.items())
            if output[venue]['dev_pass']:
                for name,part,slip in [('paired',real[real.parent_id.isin(control.parent_id)],0.),('control',control,0.),('stress',real,.0005)]:
                    more=trend.market.fund(venue,part,slip)[0]
                    records.extend(dict(policy='cancel_after_12h',venue=venue,scenario=name,period=p,**m) for p,m in more.items())
        del paths,selected,real,control,trades,eq
        gc.collect()
    pd.DataFrame(records).to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(output,indent=2,allow_nan=False)+'\n')
    print(json.dumps(output,indent=2,allow_nan=False))


if __name__=='__main__':
    main()
