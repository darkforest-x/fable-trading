"""Causal ordinal of a confirmed V1.7 key within an observable EMA trend episode."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_lock_profit as lock
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine

EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-trend-sequence-20261109-v1'


def observed_crosses(bars, start):
    """Use consecutive ready 12H closes. An unknown, flat or starting regime has no observed cross."""
    ema = engine._ema_points_12h(bars[['time','close']].to_dict('records'))
    side = np.sign(ema.ema20 - ema.ema50).where(ema.ready, 0)
    prev = side.shift(fill_value=0)
    close_ms = bars.time + engine.STEP_12H
    contiguous = close_ms.diff().eq(engine.STEP_12H)
    cross = contiguous & (side * prev < 0) & close_ms.ge(start)
    reset = ~contiguous | side.eq(0) | prev.eq(0)
    epoch = close_ms.where(cross).groupby(reset.cumsum()).ffill()
    return pd.DataFrame(dict(ema20=ema.ema20.to_numpy(),ema50=ema.ema50.to_numpy(),
                             episode=epoch.to_numpy()),index=close_ms)


def ordinals():
    fields = ['symbol','confirmed_bjt','side','ema20','ema50']
    keys = pd.read_csv(wallet.SOURCE/'signals.csv',usecols=fields,dtype={'symbol':str},
                       keep_default_na=False,float_precision='round_trip')
    keys['entry_ms'] = pd.to_datetime(keys.confirmed_bjt,utc=True).astype('datetime64[ms, UTC]').astype('int64')
    keys.index = pd.RangeIndex(1,len(keys)+1,name='source_row_number')
    keys['episode'] = np.nan
    for ticker, group in keys.groupby('symbol'):
        root = wallet.SOURCE/'inputs'/ticker
        if not (root/'bars12h.csv.gz').is_file():
            root = wallet.TOP/'inputs'/ticker
        bars = pd.read_csv(root/'bars12h.csv.gz',usecols=['time','close'],float_precision='round_trip')
        series = observed_crosses(bars,wallet.START).reindex(group.entry_ms)
        if not np.allclose(series[['ema20','ema50']],group[['ema20','ema50']],rtol=1e-10,atol=1e-14):
            raise ValueError(f'Source EMA mismatch on {ticker}')
        if not np.array_equal(np.sign(series.ema20-series.ema50),group.side):
            raise ValueError(f'Wrong cloud direction on {ticker}')
        keys.loc[group.index,'episode'] = series.episode.to_numpy()
    if keys.duplicated(['symbol','entry_ms']).any():
        raise ValueError('Duplicate key in symbol and 12H slot')
    ordered = keys.sort_values('entry_ms')
    keys['ordinal'] = ordered.groupby(['symbol','episode']).cumcount()+1
    return keys


def main():
    features = ordinals()
    features[['symbol','entry_ms','episode','ordinal']].to_csv(EXP/'signal_ordinals.csv.gz')
    paths = pd.read_csv(lock.EXP/'profit_lock_paths.csv.gz',dtype={'symbol':str},keep_default_na=False,
                        float_precision='round_trip')
    paths = paths[paths.arm_at_r.eq(2)].copy()
    seq = paths.source_row_number.map(features.ordinal)
    if not (paths.loc[~paths.is_control,'entry_ms'] ==
            paths.loc[~paths.is_control,'source_row_number'].map(features.entry_ms)).all():
        raise ValueError('Mismatched funded parent and signal sequence')
    rows, counts, arms = [], {}, {}
    for label, mask in [('covered',seq.notna()),('first',seq.eq(1)),('repeat',seq.gt(1))]:
        pool = paths[mask]
        real, ctl = pool[~pool.is_control], pool[pool.is_control]
        metrics, meta, tr, eq = wallet.evaluate(real,9)
        if meta['unquoted_position_hours'] or not ctl.parent_id.isin(real.parent_id).all():
            raise ValueError('Invalid holding marks or unmatched control')
        rows.extend(dict(policy=label,period=p,**m) for p,m in metrics.items())
        counts[label] = dict(actual=len(real),paired=len(ctl))
        arms[label] = real,ctl,tr,eq
        gc.collect()
    if counts['first']['actual']+counts['repeat']['actual']!=counts['covered']['actual']:
        raise ValueError('Observably first and repeat must partition covered opportunities')
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    dev = table[table.period.eq('dev')].sort_values('multiple',ascending=False)
    valid = dev[dev.apply(wallet.qualifies,axis=1) & dev.payoff_usdt.ge(2.5) & dev.trades.ge(100)]
    pick = valid.policy.iloc[0] if len(valid) else None
    checks = {}
    if pick is not None:
        real, ctl, tr, eq = arms[pick]
        tr.to_csv(EXP/'funded_dev_pick.csv.gz',index=False)
        eq.to_csv(EXP/'hourly_dev_pick.csv.gz',index_label='hour_end_utc')
        for name,sample,slip in [('paired',real[real.parent_id.isin(ctl.parent_id)],0.),
                                 ('control',ctl,0.),('stress',real,.0005)]:
            checks[name] = wallet.evaluate(sample,9,slip=slip)[0]
            gc.collect()
    unknown = int((seq.isna() & ~paths.is_control).sum())
    (EXP/'summary.json').write_text(json.dumps(dict(counts=counts,unknown_actual=unknown,pick=pick,checks=checks),
                                               indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__ == '__main__':
    main()
