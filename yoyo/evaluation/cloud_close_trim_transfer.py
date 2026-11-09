"""Transfer the 12H-expiring target and 20% marked-weight cap to the complete Binance archive."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_weight_trim as tested

admin = tested.admin
EXP = tested.EXP.parent/'exp-cloud-close-trim-transfer-20261109-v1'


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed Binance transfer')
    archive = admin.archive
    extra = set(json.loads((archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    paths = archive.pool(extra)
    selected,_ = tested.expiry.time_in_force(paths[~paths.is_control])
    spec = json.loads((admin.EXP/'config.json').read_text())['settlements']
    real = admin.admitted(admin.adjusted_legs(selected,extra,spec))
    reference = pd.read_csv(admin.study.EXP/'funded_tenth.csv.gz',usecols=['event_id'])
    if real.delivery.any() or set(real.event_id)!=set(reference.event_id):
        raise ValueError('The native stop/EMA holding roster or its delivery exposure changed')
    results = {}
    with archive.market_quotes(extra):
        data = archive.native.prepare(real)
        for name in ('original','trim_20pct'):
            if name=='original':
                metrics,pos,eq = admin.study.tenth.split.simulate(data,2/3,exposure=1.)
                changes = 0
            else:
                metrics,pos,eq,trims = tested.trim_account(data,archive.native.quotes,1.,.2)
                changes = trims.event_id.nunique()
                trims.to_csv(EXP/'trim_fills.csv.gz',index=False)
            if set(pos.index)!=set(reference.event_id) or not np.isclose(
                    1000+float(pos.cash_pnl.sum()),float(eq.nav.iloc[-1]),rtol=1e-8,atol=.005):
                raise ValueError('The transferred wallet failed trade or cash reconciliation')
            pos.to_csv(EXP/f'funded_{name}.csv.gz')
            eq.to_csv(EXP/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
            results[name] = dict(metrics=metrics,events_trimmed=changes)
    (EXP/'summary.json').write_text(json.dumps(results,indent=2,allow_nan=False)+'\n')
    print(json.dumps(results,indent=2),flush=True)


if __name__ == '__main__':
    main()
