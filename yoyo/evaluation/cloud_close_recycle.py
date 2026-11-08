"""Reuse actual free collateral once the 2R leg exits, preserving the cloud runner."""
import gc
import json
import zlib

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_priority as priority
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as split


EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-recycle-20261109-v1'


def assign(pool, full_slots, extra_slots):
    """Admission at the current open observes releases only from strictly earlier hours."""
    runners = pool[pool.tp_r.eq(0)].copy()
    limits = pool[pool.tp_r.eq(2)].set_index('event_id')
    if not limits.index.is_unique or set(runners.event_id) != set(limits.index):
        raise ValueError('Runner and target event sets differ')
    runners['target_end'] = runners.event_id.map(limits.exit_bar_ms)
    if runners.target_end.gt(runners.exit_bar_ms).any():
        raise ValueError('Partial leg outlasts final exit')
    runners['priority'] = runners.symbol.map(lambda s: zlib.crc32(s.encode()))
    runners = runners.sort_values(['entry_ms','entry_order','priority','symbol','event_id'])
    physical = full_slots + extra_slots
    partial_end = np.full(physical, -1, dtype=np.int64)
    final_end = partial_end.copy()
    symbols = np.full(physical, '', dtype=object)
    picks = []
    skips = dict(same_contract=0, all_slots_busy=0, admitted_while_runner_reserved=0)
    for row in runners.itertuples():
        busy = final_end >= row.entry_ms
        full = partial_end >= row.entry_ms
        if np.any(busy & (symbols == row.symbol)):
            skips['same_contract'] += 1
            continue
        free = np.flatnonzero(~busy)
        if not len(free) or full.sum() >= full_slots:
            skips['all_slots_busy'] += 1
            continue
        skips['admitted_while_runner_reserved'] += int(busy.sum() >= full_slots)
        i = int(free[0])
        final_end[i], partial_end[i], symbols[i] = row.exit_bar_ms, row.target_end, row.symbol
        picks.append((row.Index,i))
    ids = pd.DataFrame(picks,columns=['row','slot']).set_index('row')
    last = runners.loc[ids.index].assign(slot=2*ids.slot+1)
    first = limits.loc[last.event_id].reset_index()
    first['slot'] = last.slot.to_numpy()-1
    return pd.concat([last,first],ignore_index=True),skips


def evaluate(pool, full_slots, extra_slots, fraction, slip=0.):
    legs, skips = assign(pool,full_slots,extra_slots)
    data = wallet.prepare(None,2*(full_slots+extra_slots),assigned=(legs,skips))
    metrics, positions, equity = split.simulate(data,fraction,slip,full_slots=full_slots)
    return metrics,positions,equity,{**skips,'funded':len(positions),'gap_leg_hours':data['gap_marks']}


def main():
    cfg = json.loads((EXP/'config.json').read_text())
    parent = json.loads((priority.EXP/'summary.json').read_text())
    full_slots, fraction, policy = parent['slots'], parent['parent_fraction'], parent['pick']
    if (full_slots,fraction,policy)!=(9,.75,'low_prior_atr'):
        raise ValueError('Unexpected preregistered parent')
    paths = wallet.read_paths()
    paths = paths[paths.tp_r.isin([0,2])].copy()
    source = pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct'])
    source.index = pd.RangeIndex(1,len(source)+1)
    paths['entry_order'] = paths.source_row_number.map(source.prior_atr_pct).fillna(np.inf)
    real,controls = paths[~paths.is_control],paths[paths.is_control]
    old = pd.read_csv(priority.EXP/'account_metrics.csv')
    rows = old[old.policy.eq(policy)].drop(columns='policy').assign(extra_slots=0).to_dict('records')
    metadata,samples = {},{}
    for extra in cfg['extra_slots'][1:]:
        metrics,positions,equity,metadata[extra] = evaluate(real,full_slots,extra,fraction)
        rows.extend(dict(extra_slots=extra,period=period,**values) for period,values in metrics.items())
        samples[extra] = positions,equity
        gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    dev = table[table.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible = dev[dev.apply(wallet.qualifies,axis=1)&dev.trades.ge(100)]
    pick = int(eligible.extra_slots.iloc[0]) if len(eligible) else 0
    best = int(dev.extra_slots.iloc[0])
    checks = {}
    for extra in sorted({pick,best}-{0}):
        positions,equity = samples[extra]
        positions.to_csv(EXP/f'funded_{extra}.csv.gz')
        equity.to_csv(EXP/f'hourly_{extra}.csv.gz',index_label='hour_end_utc')
        checks[extra] = {}
        for name,pool,slip in [('control',controls,0.),('paired_signals',real[real.parent_id.isin(controls.parent_id)],0.),
                               ('slippage_5bp',real,.0005)]:
            checks[extra][name] = evaluate(pool,full_slots,extra,fraction,slip)[0]
            gc.collect()
    result=dict(pick=pick,best_dev_growth=best,eligible=eligible.extra_slots.tolist(),
                admission=metadata,checks=checks)
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
