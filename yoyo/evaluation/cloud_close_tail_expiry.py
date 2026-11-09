"""Retain a cloud runner when the asset's own prior return tails favor the side of the trade."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_dispersion_expiry as dispersion

tested = dispersion.tested
EXP = dispersion.EXP.parent/'exp-cloud-close-tail-expiry-20261109-v1'


def tail_asymmetry(bars):
    """Use 180 consecutive pre-key 12H close-to-close log returns; prior quantiles exclude the key."""
    returns = np.log(bars.close).diff().rolling(180,min_periods=180)
    shape = returns.quantile(.9) + returns.quantile(.1) - 2*returns.median()
    shape = shape.where(bars.index.to_series().diff(180).eq(180*dispersion.leader.BAR))
    shape.index = shape.index+2*dispersion.leader.BAR
    return shape


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed historical tail-asymmetry expiry study')
    trend = tested.expiry.trend
    paths = trend.thirds.okx_paths()
    paths = paths[~paths.is_control]
    keys = paths.loc[paths.tp_r.eq(0),['event_id','symbol','side','entry_ms']].copy()
    for symbol,group in keys.groupby('symbol'):
        history = tail_asymmetry(trend.market.market_bars('OKX',symbol))
        keys.loc[group.index,'signed_asymmetry'] = group.side.to_numpy()*history.reindex(group.entry_ms).to_numpy()
    keys.to_csv(EXP/'decisions.csv.gz',index=False)
    favor = set(keys.loc[keys.signed_asymmetry.gt(1.e-12),'event_id'])
    expiring,_ = tested.expiry.time_in_force(paths)
    real = pd.concat([expiring[expiring.event_id.isin(favor)],paths[~paths.event_id.isin(favor)]],ignore_index=True)
    data = tested.admin.study.tenth.split.pair_data(real[real.tp_r.eq(0)],real[real.tp_r.eq(2)],9)
    metrics,pos,eq,trims = tested.trim_account(data,tested.w.quotes,1.,.2)
    ref = pd.read_csv(tested.EXP/'funded_OKX.csv.gz',usecols=['event_id'])
    if set(ref.event_id)!=set(pos.index) or not np.isclose(1000+pos.cash_pnl.sum(),eq.nav.iloc[-1],atol=.005,rtol=1e-8):
        raise ValueError('Tail-based expiry changed the funded parent set or cash did not reconcile')
    pos.to_csv(EXP/'funded.csv.gz')
    eq.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
    trims.to_csv(EXP/'trim_orders.csv.gz',index=False)
    both = json.loads((dispersion.EXP/'summary.json').read_text())
    summary = dict(metrics=metrics,expiring_funded=len(set(pos.index)&favor),
                   missing_funded=int(keys.loc[keys.event_id.isin(pos.index),'signed_asymmetry'].isna().sum()),
                   always_expire=both['frozen_always_expire'],always_rest=both['always_rest']['metrics'])
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__':
    main()
