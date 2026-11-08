"""Apply an existing native post-2R protective stop to the original one-third runner."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_lock_profit as lock
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds
from yoyo.evaluation import cloud_close_volatility_expansion as market

EXP = thirds.wallet.ROOT/'experiments/active/exp-cloud-close-protect-remainder-20261109-v1'


def pair_legs():
    raw = thirds.okx_paths()
    repair.check_parent_sets(raw)
    actual_ids = set(raw.loc[~raw.is_control,'parent_id'])
    raw = raw.loc[raw.parent_id.isin(actual_ids)]
    original = raw[raw.tp_r.eq(0)].set_index('event_id').sort_index()
    target = raw[raw.tp_r.eq(2)].set_index('event_id').loc[original.index]
    locked = pd.read_csv(lock.EXP/'profit_lock_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip',keep_default_na=False)
    locked = locked[locked.arm_at_r.eq(2)&locked.tp_r.eq(0)].set_index('event_id').sort_index()
    if not locked.index.equals(original.index):
        raise ValueError('Locked native runner roster differs from corrected original signal/control identities')
    for col in ['parent_id','entry_ms','symbol','side','is_control']:
        if not locked[col].equals(original[col]):
            raise ValueError(f'Locked runner differs at {col}')
    for col in ['entry_price','stop_price','entry_order']:
        if not np.allclose(locked[col],original[col],rtol=1e-10,atol=1e-13):
            raise ValueError(f'Locked runner no longer shares original {col}')
    if (target.exit_bar_ms>locked.exit_bar_ms).any() or (locked.exit_bar_ms>original.exit_bar_ms).any():
        raise ValueError('Native child order exceeds the original risk horizon')
    phase = {'open':0,'intrabar':1,'close':2}
    if (target.exit_bar_ms.eq(locked.exit_bar_ms)&target.exit_phase.map(phase).gt(locked.exit_phase.map(phase))).any():
        raise ValueError('Target survives position close within the hour')
    if not locked.exit_fee_rate.eq(thirds.wallet.TAKER).all():
        raise ValueError('Locked runner requires the native market exit fee')
    changed = (locked.exit_bar_ms.ne(original.exit_bar_ms) | locked.exit_reason.ne(original.exit_reason)
               | ~np.isclose(locked.exit_price,original.exit_price,rtol=1e-12,atol=1e-15))
    result = pd.concat([locked.reset_index(),target.reset_index()],ignore_index=True)
    return result,set(locked.index[changed])


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Protected remainder already evaluated')
    paths,changed = pair_legs()
    real = paths[~paths.is_control]
    control = paths[paths.is_control]
    old = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    rows = old[old.venue.eq('OKX') & old.scenario.eq('actual')].assign(policy='original').to_dict('records')
    summary = dict(real_native_runners_changed=len(changed & set(real.event_id)))
    for tag,events,slip in [('actual',real,0.),('paired',real[real.parent_id.isin(control.parent_id)],0.),
                            ('control',control,0.),('stress',real,.0005)]:
        metrics,trades,eq = market.fund('OKX',events,slip)
        rows.extend(dict(policy='protect_remainder',venue='OKX',scenario=tag,period=p,**m) for p,m in metrics.items())
        if tag=='actual':
            summary.update(funded_changed=len(changed & set(trades.index)),dev_pass=second.meets(metrics['dev']),
                           full_pass=second.meets(metrics['all']),wealth_improved=metrics['all']['final_usdt']>rows[0]['final_usdt'])
            trades.to_csv(EXP/'funded.csv.gz')
            eq.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
        del trades,eq
        gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')][['policy','scenario','final_usdt','trades','win_pct','payoff_usdt','dd_bound_pct']].to_string(index=False))


if __name__=='__main__':
    main()
