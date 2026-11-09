"""Trade V1.7 when the confirmed 12H EMA band starts expanding after a contraction."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_target_expiry as expiry

EXP = expiry.EXP.parent / 'exp-cloud-close-width-release-20261109-v1'


def releases(bars):
    """Use native close/EMA20/EMA50 through the confirmed signal close, with 20 prior ready widths."""
    ema = engine._ema_points_12h(bars[['close']].reset_index().to_dict('records'))
    width = pd.Series(np.abs(ema.ema20.to_numpy()-ema.ema50.to_numpy())/bars.close.to_numpy(),
                      index=bars.index).where(ema.ready.to_numpy())
    previous = width.shift()
    minimum = previous.rolling(20,min_periods=20).min()
    valid = width.notna() & minimum.notna() & bars.index.to_series().diff(20).eq(20*engine.STEP_12H)
    selected = (previous.eq(minimum) & width.gt(previous)).astype(float).where(valid)
    selected.index = selected.index + engine.STEP_12H
    return selected


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed compression-release comparison')
    trend = expiry.trend
    paths = trend.thirds.okx_paths()
    trend.repair.check_parent_sets(paths)
    pool,_ = expiry.time_in_force(paths)
    events = pool.loc[pool.tp_r.eq(0),['event_id','entry_ms','parent_id','symbol','is_control']].copy()
    value = pd.Series(np.nan,index=events.index)
    for symbol,group in events.groupby('symbol'):
        value.loc[group.index] = releases(trend.market.market_bars('OKX',symbol)).reindex(group.entry_ms).to_numpy()
    events['release'] = value
    events.to_csv(EXP/'decisions.csv.gz',index=False)
    summary = {}
    for name,ids in [('covered',events.loc[value.notna(),'event_id']),('release',events.loc[value.eq(1),'event_id'])]:
        cohort = pool[pool.event_id.isin(ids)]
        real = cohort[~cohort.is_control]
        if name=='covered' and len(real)==int((~pool.is_control).sum()):
            ref = pd.read_csv(expiry.EXP/'account_metrics.csv')
            ref = ref[ref.venue.eq('OKX') & ref.scenario.eq('actual') & ref.policy.eq('cancel_after_12h')]
            metrics = ref.drop(columns=['venue','scenario','policy']).set_index('period').to_dict('index')
            if len(metrics)!=4:
                raise ValueError('Frozen OKX baseline did not supply all four periods')
        else:
            metrics,positions,equity = trend.market.fund('OKX',real,0.)
            positions.to_csv(EXP/f'funded_{name}.csv.gz')
            equity.to_csv(EXP/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
        summary[name] = dict(metrics=metrics,eligible=len(real)//2)
        if name=='release' and trend.second.meets(metrics['dev']) and metrics['dev']['final_usdt']>summary['covered']['metrics']['dev']['final_usdt']:
            controls = cohort[cohort.is_control & cohort.parent_id.isin(real.parent_id)]
            paired = real[real.parent_id.isin(controls.parent_id)]
            for label,subset,slip in [('paired',paired,0.),('control',controls,0.),('stress',real,.0005)]:
                summary[label]=dict(metrics=trend.market.fund('OKX',subset,slip)[0],eligible=len(subset)//2)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__':
    main()
