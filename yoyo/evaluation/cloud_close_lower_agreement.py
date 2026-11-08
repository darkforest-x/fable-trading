"""Screen each original Binance 12H key by a closed, same-week lower-timeframe V1.7 key."""
import gc
import json

import pandas as pd

from yoyo.evaluation import cloud_close_binance_transfer as venue
from yoyo.evaluation import cloud_close_reward_account as wallet


EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-lower-agreement-20261109-v1'


def prior_signal_times(paths, frames, replacements=None):
    """Use only same-contract/NY-week/side lower keys with close_ns <= entry_ms * 1e6."""
    files = sorted((venue.ARCHIVE/'results/symbols').glob('*/signals.csv.gz'))
    columns = ['signal_id','symbol','week','side','tf','close_ns']
    parts = [pd.read_csv(file, usecols=columns, dtype={'symbol':str}) for file in files
             if replacements is None or file.parent.name not in replacements]
    if replacements is not None:
        parts.extend(frame[columns] for frame in replacements.values())
    signals = pd.concat(parts,ignore_index=True)
    if len(files) != 632 or not signals.signal_id.is_unique:
        raise ValueError('Frozen signal identities changed')
    anchors = signals[signals.tf.eq('12H')].set_index('signal_id')
    indexed = paths[['parent_id','side','symbol','entry_ms','is_control']].copy()
    indexed['week'] = indexed.parent_id.map(anchors.week)
    if indexed.week.isna().any() or not indexed.side.eq(indexed.parent_id.map(anchors.side)).all():
        raise ValueError('Missing parent week or inconsistent direction')
    indexed['asset'] = indexed.symbol + 'USDT'
    if not indexed.asset.eq(indexed.parent_id.map(anchors.symbol)).all():
        raise ValueError('Contract name differs across archives')
    real = indexed[~indexed.is_control]
    if not (real.entry_ms * 1_000_000).eq(real.parent_id.map(anchors.close_ns)).all():
        raise ValueError('12H key clock differs from native entry')
    # The frozen matcher already verified the control and its parent belong to this same NY week.
    idx = pd.MultiIndex.from_frame(indexed[['asset','week','side']])
    for frame in frames:
        own = signals[signals.tf.eq(frame)]
        first = own.groupby(['symbol','week','side']).close_ns.min()
        indexed[frame] = first.reindex(idx).to_numpy() <= indexed.entry_ms.to_numpy()*1_000_000
    return indexed


def main():
    frames = json.loads((EXP/'config.json').read_text())['timeframes']
    fills = pd.read_csv(venue.EXP/'fills.csv.gz', dtype={'symbol':str}, keep_default_na=False,
                        float_precision='round_trip')
    paths = fills[fills.eligible].copy()
    paths = paths[paths.parent_id.isin(paths.loc[~paths.is_control,'parent_id'])]
    flags = prior_signal_times(paths, frames)
    baseline = pd.read_csv(venue.EXP/'account_metrics.csv')
    rows = baseline[baseline.policy.eq('partial75')].assign(policy='all').to_dict('records')
    counts = {}
    def selected(frame):
        pool = paths[flags[frame]]
        real = pool[~pool.is_control]
        return real, pool[pool.is_control & pool.parent_id.isin(real.parent_id)]
    for frame in frames:
        real, control = selected(frame)
        counts[frame] = dict(signals=len(real)//2, controls=len(control)//2)
        (metrics, _, _), gaps = venue.account(real, True)
        counts[frame]['unquoted_leg_hours'] = gaps
        rows.extend(dict(policy=frame, period=p, **m) for p,m in metrics.items())
        gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv', index=False)
    development = table[table.period.eq('dev')].sort_values('multiple', ascending=False)
    eligible = development[development.apply(wallet.qualifies,axis=1) & development.payoff_usdt.ge(2.5)
                           & development.trades.ge(100)]
    pick = None if eligible.empty else eligible.policy.iloc[0]
    checks = {}
    if pick in frames:
        real, control = selected(pick)
        matched = real[real.parent_id.isin(control.parent_id)]
        for key, pool, slip in [('paired_signals',matched,0.),('control',control,0.),('slippage_5bp',real,.0005)]:
            (checks[key], _, _), _ = venue.account(pool, True, slip)
            gc.collect()
    (EXP/'summary.json').write_text(json.dumps(dict(pick=pick, counts=counts, checks=checks),
                                                indent=2, allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__ == '__main__':
    main()
