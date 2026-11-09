"""Screen native 12H reclaims on the asset's key-bar return after adjusting for BTC."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_weight_trim as trim

EXP = trim.EXP.parent/'exp-cloud-close-btc-residual-20261109-v1'
BAR = 12*trim.w.HOUR


def residual_return(asset, btc):
    """Estimate beta from 180 completed pre-key 12H close returns; observe only the current open/close thereafter."""
    bars = pd.concat([asset[['open','close']],btc[['open','close']].add_prefix('btc_')],axis=1).sort_index()
    returns = np.log(bars[['close','btc_close']]).diff()
    variance = returns.btc_close.rolling(180,min_periods=180).var().shift(1)
    cov = returns.close.rolling(180,min_periods=180).cov(returns.btc_close).shift(1)
    # 180 prior returns require 181 previous closes. The current 12H key
    # opening cannot be substituted for any return in the regression window.
    complete = bars.index.to_series().diff(181).eq(181*BAR) & variance.gt(0.)
    value = np.log(bars.close/bars.open) - cov/variance * np.log(bars.btc_close/bars.btc_open)
    value = value.where(complete)
    value.index = value.index+BAR
    return value


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed BTC-adjusted confirmation return comparison')
    trend = trim.expiry.trend
    paths, _ = trim.expiry.time_in_force(trend.thirds.okx_paths())
    keys = paths.loc[~paths.is_control & paths.tp_r.eq(0),['event_id','entry_ms','symbol','side']].copy()
    btc = trend.market.market_bars('OKX','BTC')
    for symbol, group in keys.groupby('symbol'):
        observations = residual_return(trend.market.market_bars('OKX',symbol),btc)
        if symbol == 'BTC':
            observations.loc[observations.notna()] = 0.
        keys.loc[group.index,'signed_residual'] = group.side.to_numpy()*observations.reindex(group.entry_ms).to_numpy()
    keys.to_csv(EXP/'decisions.csv.gz',index=False)
    result = {}
    for name,mask in [('covered',keys.signed_residual.notna()),('positive',keys.signed_residual.gt(1e-12))]:
        chosen = paths[paths.event_id.isin(keys.loc[mask,'event_id'])]
        if name=='covered' and mask.all():
            summary = json.loads((trim.EXP/'summary.json').read_text())['OKX']
        else:
            data = trim.admin.study.tenth.split.pair_data(chosen[chosen.tp_r.eq(0)],chosen[chosen.tp_r.eq(2)],9)
            metrics,pos,eq,trims = trim.trim_account(data,trim.w.quotes,1.,.2)
            pos.to_csv(EXP/f'funded_{name}.csv.gz')
            eq.to_csv(EXP/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
            trims.to_csv(EXP/f'trims_{name}.csv.gz',index=False)
            summary = dict(metrics=metrics,trim_fills=len(trims))
        result[name] = dict(eligible=int(mask.sum()),**summary)
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__ == '__main__':
    main()
