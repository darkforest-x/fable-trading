"""Nine original cloud slots with ten equal NAV shares as the new-order size limit."""
import gc
import json
from contextlib import nullcontext

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_archive_cohort as archive
from yoyo.evaluation import cloud_close_efficient_trend as trend
from yoyo.evaluation import cloud_close_scaleout as split

EXP=archive.EXP.parent/'exp-cloud-close-tenth-sizing-20261109-v1'


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Funded NAV-tenth experiment already exists')
    extra=set(json.loads((archive.EXP/'summary.json').read_text())['archived_additions'])
    records=[]; output={}
    for venue in ['OKX','Binance']:
        if venue=='OKX':
            paths=trend.thirds.okx_paths()
            trend.repair.check_parent_sets(paths)
            baseline=pd.read_csv(trend.EXP/'funded_OKX.csv.gz').set_index('event_id')
        else:
            paths=archive.pool(extra)
            baseline=pd.read_csv(archive.EXP/'funded_trend_efficiency.csv.gz').set_index('event_id')
        with (archive.market_quotes(extra) if venue=='Binance' else nullcontext()):
            _,selected=trend.inputs(venue,paths)
            real=selected[~selected.is_control]
            control=selected[selected.is_control & selected.parent_id.isin(real.parent_id)]
            for tag,sample in [('actual',real),('paired',real[real.parent_id.isin(control.parent_id)]),('control',control)]:
                if venue=='OKX':
                    data=split.pair_data(sample[sample.tp_r.eq(0)],sample[sample.tp_r.eq(2)],9)
                else:
                    data=archive.native.prepare(sample)
                for slip in ([0.,.0005] if tag=='actual' else [0.]):
                    name='stress' if slip else tag
                    metrics,pos,eq=split.simulate(data,2/3,slip,exposure=.9)
                    if not np.isclose(1000+pos.cash_pnl.sum(),eq.nav.iloc[-1],atol=2e-4,rtol=1e-9):
                        raise ValueError('Native cash flow failed to reconcile')
                    records.extend(dict(venue=venue,scenario=name,period=p,**m) for p,m in metrics.items())
                    if name=='actual':
                        if set(pos.index)!=set(baseline.index) or not np.allclose(pos.net_return,baseline.loc[pos.index,'net_return'],rtol=1e-8,atol=1e-10):
                            raise ValueError('Changing the nominal cap changed the roster or original unit returns')
                        pos.to_csv(EXP/f'funded_{venue}.csv.gz')
                        eq.to_csv(EXP/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
                        output[venue]=dict(full=metrics['all'],dev=metrics['dev'],dev_pass=trend.second.meets(metrics['dev']),
                                           full_pass=trend.second.meets(metrics['all']))
                del data,pos,eq
                gc.collect()
        del paths,selected,real,control
        gc.collect()
    pd.DataFrame(records).to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(output,indent=2,allow_nan=False)+'\n')
    print(json.dumps(output,indent=2,allow_nan=False))


if __name__=='__main__':
    main()
