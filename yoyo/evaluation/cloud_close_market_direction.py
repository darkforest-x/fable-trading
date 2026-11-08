"""Admit a V1.7 12H position when other observable contracts share its past direction."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_lock_profit as lock
from yoyo.evaluation import cloud_close_reward_account as wallet

EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-market-direction-20261109-v1'


def returns_panel():
    coverage = pd.read_csv(wallet.SOURCE / 'input_coverage.csv', keep_default_na=False)
    returns = {}
    for row in coverage.itertuples():
        source = wallet.TOP if row.cohort == 'top20_reused' else wallet.SOURCE
        bars = pd.read_csv(source / 'inputs' / row.symbol / 'bars12h.csv.gz', usecols=['time', 'close'])
        returns[row.symbol] = leader.trailing_return(bars)
    return pd.DataFrame(returns).rename_axis(index='entry_ms', columns='symbol')


def peer_fraction(events, panel):
    """At entry_ms, count other complete 90-day returns using only candles closed before the key."""
    measured = panel.notna().sum(axis=1)
    keys = pd.MultiIndex.from_frame(events[['entry_ms', 'symbol']])
    own = panel.stack().reindex(keys).to_numpy()
    peers = events.entry_ms.map(measured).to_numpy() - 1
    if np.isnan(own).any() or (peers < 9).any():
        raise ValueError('Parent does not have the frozen 90-day coverage')
    positive = events.entry_ms.map(panel.gt(0).sum(axis=1)).to_numpy() - (own > 0)
    negative = events.entry_ms.map(panel.lt(0).sum(axis=1)).to_numpy() - (own < 0)
    return np.where(events.side.eq(1), positive, negative) / peers


def main():
    cfg = json.loads((EXP/'config.json').read_text())
    parent = pd.read_csv(lock.EXP/'account_metrics.csv')
    rows = parent[parent.arm_at_r.eq(2)].drop(columns='arm_at_r').assign(peer_threshold=0.).to_dict('records')
    paths = pd.read_csv(lock.EXP/'profit_lock_paths.csv.gz', dtype={'symbol': str},
                        float_precision='round_trip', keep_default_na=False)
    paths = paths[paths.arm_at_r.eq(2.)].copy()
    paths['peer_fraction'] = peer_fraction(paths, returns_panel())
    if not paths.peer_fraction.between(0, 1).all() or paths.duplicated('event_id').any():
        raise ValueError('Invalid peer direction data')
    paths[['event_id','entry_ms','symbol','side','peer_fraction']].to_csv(EXP/'observed_fractions.csv.gz',index=False)
    counts, arms = {}, {}
    for threshold in cfg['thresholds']:
        sample = paths[paths.peer_fraction.ge(threshold)]
        real = sample[~sample.is_control]
        ctl = sample[sample.is_control & sample.parent_id.isin(real.parent_id)]
        metrics, meta, trades, equity = wallet.evaluate(real, 9)
        if meta['unquoted_position_hours'] != 0:
            raise ValueError('Unmarked holding hours')
        arms[threshold] = real, ctl, trades, equity
        counts[threshold] = dict(actual=len(real), paired=len(ctl))
        rows.extend(dict(peer_threshold=threshold, period=period, **values) for period, values in metrics.items())
        gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv', index=False)
    dev = table[table.period.eq('dev')]
    valid = dev[dev.apply(wallet.qualifies,axis=1) & dev.payoff_usdt.ge(2.5) & dev.trades.ge(100)]
    pick = None if valid.empty else float(valid.sort_values('multiple',ascending=False).peer_threshold.iloc[0])
    checks = {}
    if pick in arms:
        real, ctl, trades, equity = arms[pick]
        trades.to_csv(EXP/'funded_dev_pick.csv.gz',index=False)
        equity.to_csv(EXP/'hourly_dev_pick.csv.gz',index_label='hour_end_utc')
        for label, pool, slip in [('paired', real[real.parent_id.isin(ctl.parent_id)], 0.),
                                  ('control', ctl, 0.), ('stress', real, .0005)]:
            checks[label] = wallet.evaluate(pool,9,slip=slip)[0]
            gc.collect()
    (EXP/'summary.json').write_text(json.dumps(dict(counts=counts,pick=pick,checks=checks),
                                               indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__ == '__main__':
    main()
