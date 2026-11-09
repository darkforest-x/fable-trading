"""Apply the previously fixed single-name weight cap at actual hourly closes."""
import gc
import json
from contextlib import nullcontext

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_weight_trim as tested

EXP = tested.EXP.parent/'exp-cloud-close-hourly-trim-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed hourly weight-cap study')
    archive = tested.admin.archive
    extra = set(json.loads((archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((tested.admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    prev = json.loads((tested.EXP/'summary.json').read_text())
    summary = {}
    for venue in ('Binance','OKX'):
        with (archive.market_quotes(extra) if venue=='Binance' else nullcontext()):
            if venue=='Binance':
                data,frozen = tested.binance_inputs(extra)
                loader,exposure = archive.native.quotes,.9
            else:
                paths,_ = tested.expiry.time_in_force(tested.expiry.trend.thirds.okx_paths())
                real = paths[~paths.is_control]
                data = tested.admin.study.tenth.split.pair_data(real[real.tp_r.eq(0)],real[real.tp_r.eq(2)],9)
                frozen = pd.read_csv(tested.EXP/'funded_OKX.csv.gz').set_index('event_id')
                loader,exposure = tested.w.quotes,1.
            metrics,pos,eq,trims = tested.trim_account(data,loader,exposure,.2,review_hours=1)
        if set(pos.index)!=set(frozen.index) or not np.isclose(1000+pos.cash_pnl.sum(),eq.nav.iloc[-1],atol=.005,rtol=1e-8):
            raise ValueError('Hourly-only management changed entry roster or cash reconciliation')
        pos.to_csv(EXP/f'funded_{venue}.csv.gz')
        eq.to_csv(EXP/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
        trims.to_csv(EXP/f'orders_{venue}.csv.gz',index=False)
        # Two simultaneous child reductions can be executed as one parent order.
        size = trims.assign(gross=trims['size']*trims.price).groupby(['event_id','hour']).gross.sum()
        summary[venue] = dict(metrics=metrics, reference_12h=prev[venue]['metrics'],
                              trimmed_positions=trims.event_id.nunique(),parent_reductions=len(size),
                              gross_trim_turnover_usdt=float(size.sum()),below_5u_parent_reductions=int(size.lt(5).sum()),
                              sub_5u_turnover_usdt=float(size[size.lt(5)].sum()))
        del data,pos,eq,trims
        gc.collect()
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__':
    main()
