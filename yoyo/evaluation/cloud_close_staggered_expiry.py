"""Keep half of the original resting 2R target after the first confirmed 12H interval."""
import gc
import json
from contextlib import nullcontext

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_weight_trim as tested

admin = tested.admin
EXP = tested.EXP.parent/'exp-cloud-close-staggered-expiry-20261109-v1'


def prepare(paths, venue, loader):
    expiring,_ = tested.expiry.time_in_force(paths)
    runners,skips = tested.w.admit(paths[paths.tp_r.eq(0)],9)
    pieces = []
    # Three equal child quantities are opened before target timing is known.
    for i,events in enumerate([expiring[expiring.tp_r.eq(2)],paths[paths.tp_r.eq(2)],runners]):
        child = events.set_index('event_id',drop=False).loc[runners.event_id].copy()
        if (child.exit_bar_ms.to_numpy() > runners.exit_bar_ms.to_numpy()).any():
            raise ValueError('Partial outlasts the original runner')
        child['slot'] = 3*runners.slot.to_numpy()+i
        pieces.append(child)
    dates = {} if venue=='OKX' else dict(start=admin.archive.venue.START,end=admin.archive.venue.END)
    return tested.w.prepare(None,27,assigned=(pd.concat(pieces,ignore_index=True),skips),quote_loader=loader,**dates)


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Staggered expiry study already completed')
    archive = admin.archive
    extra = set(json.loads((archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    summary = {}
    for venue in ['OKX','Binance']:
        if venue=='OKX':
            paths = tested.expiry.trend.thirds.okx_paths()
            real = paths[~paths.is_control]
            comparison = tested.EXP/'funded_OKX.csv.gz'
        else:
            paths = archive.pool(extra)
            spec = json.loads((admin.EXP/'config.json').read_text())['settlements']
            real = admin.admitted(admin.adjusted_legs(paths[~paths.is_control],extra,spec))
            if real.delivery.any():
                raise ValueError('Selected roster requires a delivery mark')
            comparison = EXP.parent/'exp-cloud-close-trim-transfer-20261109-v1/funded_trim_20pct.csv.gz'
        with (archive.market_quotes(extra) if venue=='Binance' else nullcontext()):
            loader = archive.native.quotes if venue=='Binance' else tested.w.quotes
            data = prepare(real,venue,loader)
            metrics,pos,eq,trims = tested.trim_account(data,loader,1.,.2,(1/3,1/3,1/3))
        ref = pd.read_csv(comparison).set_index('event_id')
        if set(pos.index)!=set(ref.index) or not np.isclose(1000+pos.cash_pnl.sum(),eq.nav.iloc[-1],atol=.005,rtol=1e-8):
            raise ValueError('Staggered account roster or cash failed reconciliation')
        pos.to_csv(EXP/f'funded_{venue}.csv.gz')
        eq.to_csv(EXP/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
        trims.to_csv(EXP/f'trims_{venue}.csv.gz',index=False)
        summary[venue] = dict(metrics=metrics,reference_final=1000+ref.cash_pnl.sum(),trimmed_events=trims.event_id.nunique())
        del data, paths, real, pos, eq, trims
        gc.collect()
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__':
    main()
