"""Ratchet the existing V1.7 profit stop using a share of past completed H1 excursion."""
import gc
import json

import pandas as pd

from yoyo.evaluation import cloud_close_lock_profit as lock
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_trail as trail


EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-peak-retention-20261109-v1'


def main():
    config = json.loads((EXP / 'config.json').read_text())
    fractions = config['fractions']
    parent = pd.read_csv(lock.EXP / 'profit_lock_paths.csv.gz', dtype={'symbol': str},
                         keep_default_na=False, float_precision='round_trip')
    parent = parent[parent.arm_at_r.eq(2)]
    if parent.event_id.duplicated().any() or not parent.exit_fee_rate.eq(wallet.TAKER).all():
        raise ValueError('Expected unique market-only +2R-armed parent positions')
    file = EXP / 'paths.csv.gz'
    if file.exists():
        paths = pd.read_csv(file, dtype={'symbol': str}, keep_default_na=False,
                            float_precision='round_trip')
    else:
        paths = pd.concat([trail.symbol_paths(group, fractions, fixed_profit=.5, protect_peak=True)
                           for _, group in parent.groupby('symbol')], ignore_index=True)
        pending = file.with_suffix('.pending')
        paths.to_csv(pending, index=False, compression='gzip')
        pending.replace(file)
    keys = pd.MultiIndex.from_product([parent.event_id, fractions])
    if not keys.sort_values().equals(pd.MultiIndex.from_frame(paths[['event_id','peak_retained']]).sort_values()):
        raise ValueError('Candidate event coverage differs from parent')
    original_end = paths.event_id.map(parent.set_index('event_id').exit_bar_ms)
    if paths.exit_bar_ms.gt(original_end).any() or paths.exit_bar_ms.lt(paths.entry_ms).any():
        raise ValueError('Overlay outside original protected position lifetime')
    reference = pd.read_csv(lock.EXP / 'account_metrics.csv')
    metrics = reference[reference.arm_at_r.eq(2)].drop(columns='arm_at_r')
    rows = metrics.assign(peak_retained=0.).to_dict('records')
    metadata = {}
    for fraction in fractions:
        real = paths[paths.peak_retained.eq(fraction) & ~paths.is_control]
        periods, meta, _, _ = wallet.evaluate(real, 9)
        rows.extend(dict(peak_retained=fraction, period=period, **values) for period, values in periods.items())
        metadata[fraction] = meta
        gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP / 'account_metrics.csv', index=False)
    dev = table[table.period.eq('dev')].sort_values('final_usdt', ascending=False)
    eligible = dev[dev.apply(wallet.qualifies, axis=1) & dev.payoff_usdt.ge(2.5) & dev.trades.ge(100)]
    pick = None if eligible.empty else float(eligible.iloc[0].peak_retained)
    checks = {}
    if pick in fractions:
        chosen = paths[paths.peak_retained.eq(pick)]
        real, control = chosen[~chosen.is_control], chosen[chosen.is_control]
        matched = real[real.parent_id.isin(control.parent_id)]
        for key, pool, slip in [('paired_signals', matched, 0.), ('control', control, 0.),
                                ('slippage_5bp', real, .0005)]:
            checks[key] = wallet.evaluate(pool, 9, slip=slip)[0]
    summary = dict(pick=pick, metadata=metadata, checks=checks)
    (EXP / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__ == '__main__':
    main()
