"""Allow a larger runner on original 12H entries confirmed by a same-week 4H signal."""
import gc
import json

import pandas as pd

from yoyo.evaluation import cloud_close_binance_transfer as venue
from yoyo.evaluation import cloud_close_lower_agreement as agreement
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as scaleout


EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-agreement-split-20261109-v1'


def split_data(pool):
    return scaleout.pair_data(pool[pool.tp_r.eq(0)], pool[pool.tp_r.eq(2)], 9, start=venue.START,
                              end=venue.END, quote_loader=venue.quotes)


def simulate(pool, fraction, slip=0.):
    if fraction == 0:
        return venue.account(pool, False, slip)[0]
    return scaleout.simulate(split_data(pool), fraction, slip)


def main():
    cfg = json.loads((EXP/'config.json').read_text())
    all_paths = pd.read_csv(venue.EXP/'fills.csv.gz', keep_default_na=False, dtype={'symbol':str},
                            float_precision='round_trip')
    paths = all_paths[all_paths.eligible]
    paths = paths[paths.parent_id.isin(paths.loc[~paths.is_control,'parent_id'])]
    pool = paths[agreement.prior_signal_times(paths,['4H'])['4H']]
    real = pool[~pool.is_control]
    control = pool[pool.is_control & pool.parent_id.isin(real.parent_id)]
    previous = json.loads((agreement.EXP/'summary.json').read_text())['counts']['4H']
    if len(real)//2 != previous['signals'] or len(control)//2 != previous['controls']:
        raise ValueError('Parent signal/control eligibility differs')
    baseline = pd.read_csv(agreement.EXP/'account_metrics.csv')
    rows = baseline[baseline.policy.eq('4H')].drop(columns='policy').assign(fraction=.75).to_dict('records')
    data = split_data(real)
    curves = {}
    for fraction in cfg['fractions']:
        if fraction == .75:
            continue
        metrics, trades, equity = (scaleout.simulate(data, fraction) if fraction else
                                    venue.account(real, False)[0])
        rows.extend(dict(fraction=fraction,period=p,**m) for p,m in metrics.items())
        curves[fraction] = trades, equity
        gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    dev = table[table.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible = dev[dev.apply(wallet.qualifies,axis=1) & dev.payoff_usdt.ge(2.5) & dev.trades.ge(100)]
    pick = None if eligible.empty else float(eligible.fraction.iloc[0])
    checks = {}
    if pick in curves:
        trades, equity = curves[pick]
        trades.to_csv(EXP/'funded_dev_pick.csv.gz')
        equity.to_csv(EXP/'hourly_dev_pick.csv.gz', index_label='hour_end_utc')
        matched = real[real.parent_id.isin(control.parent_id)]
        for key, events, slip in [('paired_signals',matched,0.),('controls',control,0.),
                                  ('slippage_5bp',real,.0005)]:
            checks[key] = simulate(events,pick,slip)[0]
    (EXP/'summary.json').write_text(json.dumps(dict(pick=pick, checks=checks, gap_leg_hours=data['gap_marks']),
                                                indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
