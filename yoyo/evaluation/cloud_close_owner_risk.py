"""Screen the hourly name cap under the revised 20% win and 50% drawdown limits."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_fvg_expiry as checked
from yoyo.evaluation import cloud_close_hourly_trim as hourly
from yoyo.evaluation import cloud_close_scaleout as split

EXP = hourly.EXP.parent / 'exp-cloud-close-owner-risk-20261109-v1'


def qualifies(metrics, rule):
    return (metrics['trades'] >= 100 and metrics['win_pct'] >= rule['win_pct_min']
            and metrics['payoff_usdt'] >= rule['research_payoff_usdt_min']
            and metrics['dd_bound_pct'] <= rule['dd_pct_max'])


def simulate(data, cap, name, *, slip=0., ids=None):
    loader = hourly.tested.w.quotes
    if slip:
        result = checked._trim_account_with_slippage(data, loader, 1., cap, review_hours=1, market_slip=slip)
    else:
        result = hourly.tested.trim_account(data, loader, 1., cap, review_hours=1)
    metrics, positions, equity, _ = result
    if ids is not None and set(positions.index) != ids:
        raise ValueError(f'{name}: hourly sizing changed original funded IDs')
    if not np.isclose(1000 + positions.cash_pnl.sum(), equity.nav.iat[-1], atol=.005, rtol=1.e-8):
        raise ValueError(f'{name}: VectorBT cash and trade receipts differ')
    positions.to_csv(EXP/f'funded_{name}.csv.gz')
    equity.to_csv(EXP/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
    return metrics


def prepare(rows):
    return split.pair_data(rows[rows.tp_r.eq(0)], rows[rows.tp_r.eq(2)], 9)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed revised-risk study')
    rule = json.loads((EXP/'config.json').read_text())['owner_acceptance']
    orig = hourly.tested.expiry.trend.thirds.okx_paths()
    hourly.tested.expiry.trend.repair.check_parent_sets(orig)
    tape, _ = hourly.tested.expiry.time_in_force(orig)
    real = tape[~tape.is_control]
    later = tape[tape.is_control & tape.parent_id.isin(real.parent_id)]
    paired = real[real.parent_id.isin(later.parent_id)]
    frozen = pd.read_csv(hourly.EXP/'funded_OKX.csv.gz',usecols=['event_id']).event_id
    ids = set(frozen)
    full = prepare(real)
    metrics = {'fifth':json.loads((hourly.EXP/'summary.json').read_text())['OKX']['metrics']}
    caps = {'sixth':1/6,'fifth':.20,'quarter':.25,'third':1/3}
    for name, cap in caps.items():
        if name != 'fifth':
            metrics[name] = simulate(full, cap, name, ids=ids)
    valid = [name for name in caps if qualifies(metrics[name]['dev'],rule)]
    pick = max(valid,key=lambda name:metrics[name]['dev']['final_usdt']) if valid else None
    checks = {}
    names = {'fifth',pick} - {None}
    for name in sorted(names):
        checks[name] = {'extra_5bp':simulate(full,caps[name],name+'_stress',slip=.0005,ids=ids)}
    del full
    for label, rows in [('paired',paired),('later',later)]:
        pool = prepare(rows)
        for name in sorted(names):
            checks[name][label] = simulate(pool,caps[name],name+'_'+label)
        del pool
    summary = dict(owner_acceptance=rule, original_opportunities=len(real)//2, matched_pairs=len(paired)//2,
                   original_funded_positions=len(ids), cap_fraction=caps, development_pick=pick,
                   metrics=metrics, checks=checks, full_risk_payoff_pass={n:qualifies(m['all'],rule) for n,m in metrics.items()},
                   full_wealth_pass={n:m['all']['final_usdt']>rule['terminal_usdt_strictly_above'] for n,m in metrics.items()},
                   fees='OKX maker 2bp / market 5bp, before funding; 5bp adverse market-fill stress shown separately')
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print('Development pick',pick,'eligible',valid,flush=True)


if __name__ == '__main__':
    main()
