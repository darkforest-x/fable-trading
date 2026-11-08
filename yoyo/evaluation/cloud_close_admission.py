"""One-at-a-time entry screens using only V1.7 confirmation-time columns."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_reward_account as wallet

EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-admission-20261109-v1'


def gates(signals):
    """Causal source columns from the completed key, prior 14-bar relative
    return and that NY Monday; count only same-side signals at the same close.
    """
    same_bar = signals.groupby(['confirmed_bjt','side']).symbol.transform('count')
    return dict(all=pd.Series(True,index=signals.index),long=signals.side.eq(1),
        short=signals.side.eq(-1),btc_aligned=signals.btc_same_side.eq(1),
        btc_opposite=signals.btc_same_side.eq(0),relative_strength_positive=signals.rel_strength_14.gt(0),
        tight_reclaim=signals.close_distance_r.le(.5),same_side_stoch=signals.same_side_stoch,
        large_wick=signals.wick_share.ge(.5),wide_monday=signals.monday_width_pct.ge(5),
        strong_cloud=signals.ema_spread_atr.ge(1),prior_market_breadth=same_bar.ge(5))


def comparisons(signal_paths, controls, weeks):
    """Per-parent equal-notional fee-net returns and matched week-cluster bootstrap.
    Actual close and non-signal control occur at different times in the same NY week.
    """
    def returns(rows):
        ratio = rows.exit_price/rows.entry_price
        return pd.Series((rows.side*(ratio-1)-wallet.TAKER-ratio*rows.exit_fee_rate).to_numpy(),
                         index=rows.parent_id)
    ret = returns(signal_paths)
    pairs = pd.concat([ret.rename('signal'),returns(controls).rename('control'),weeks],axis=1,
                      join='inner').dropna()
    pairs['d'] = pairs.signal - pairs.control
    by_week = pairs.groupby('week').d.agg(['sum','size']).to_numpy()
    rng = np.random.default_rng(20261109)
    if not len(by_week):
        return {'pairs':0}
    draws = rng.integers(len(by_week),size=(2000,len(by_week)))
    sums = by_week[draws].sum(axis=1)
    ci = np.quantile(sums[:,0]/sums[:,1],[.025,.975])*100
    count = max(1,int(np.ceil(len(ret)*.01)))
    return dict(signals=len(ret),pairs=len(pairs),weeks=len(by_week),
                matched_signal_mean_pct=100*pairs.signal.mean(),control_mean_pct=100*pairs.control.mean(),
                difference_pct=100*pairs.d.mean(),week_ci_pct=ci.tolist(),
                signal_mean_pct=100*ret.mean(),excluding_best_one_pct_mean_pct=100*(ret.sort_values().iloc[:-count].mean()))


def main():
    parent_cfg = json.loads((wallet.EXP/'account_summary.json').read_text())
    chosen = int(parent_cfg['pick'])
    src = pd.read_csv(wallet.SOURCE/'signals.csv',float_precision='round_trip')
    src.index = pd.Index(['signal-v17-row-'+str(i+1) for i in range(len(src))],name='parent_id')
    paths = pd.read_csv(wallet.EXP/'paths.csv.gz',float_precision='round_trip',keep_default_na=False)
    paths = paths[paths.tp_r.eq(chosen)]
    real, controls = paths[~paths.is_control], paths[paths.is_control]
    if not real.parent_id.is_unique or not controls.parent_id.is_unique:
        raise ValueError('Nonunique parent within arm')
    aligned = src.loc[real.parent_id]
    if not np.array_equal(aligned.symbol.to_numpy(),real.symbol.to_numpy()):
        raise ValueError('Path source identity mismatch')
    rules = gates(src)
    if list(rules)!=json.loads((EXP/'config.json').read_text())['eligibility']:
        raise ValueError('Eligibility roster differs from registration')
    rows = []
    parent = pd.read_csv(wallet.EXP/'account_metrics.csv')
    meta = {'all':parent_cfg['admission'][str(chosen)]}
    checks = {}
    samples = {}
    for name,mask in rules.items():
        eligible = src.index[mask.fillna(False)]
        pool = real[real.parent_id.isin(eligible)]
        null = controls[controls.parent_id.isin(pool.parent_id)]
        checks[name] = comparisons(pool,null,src.week)
        if name=='all':
            rows.extend(parent[parent.tp_r.eq(chosen)].drop(columns='tp_r').assign(rule=name).to_dict('records'))
            continue
        m, meta[name], trades, eq = wallet.evaluate(pool)
        samples[name] = (trades, eq)
        rows.extend(dict(rule=name,period=period,**values) for period,values in m.items())
    results = pd.DataFrame(rows)
    results.to_csv(EXP/'account_metrics.csv',index=False)
    dev = results[results.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible = dev[dev.apply(wallet.qualifies,axis=1)&dev.trades.ge(100)]
    pick = 'all' if eligible.empty else eligible.rule.iloc[0]
    best = dev.rule.iloc[0]
    tests = {}
    for name in sorted({pick,best}-{'all'}):
        trades,eq = samples[name]
        trades.to_csv(EXP/f'funded_{name}.csv.gz',index=False)
        eq.to_csv(EXP/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
        ids = src.index[rules[name].fillna(False)]
        pool = real[real.parent_id.isin(ids)]
        null = controls[controls.parent_id.isin(pool.parent_id)]
        tests[name] = {}
        for label,events,slip in [('control',null,0.),('paired_signals',pool[pool.parent_id.isin(null.parent_id)],0.),
                                  ('slippage_5bp',pool,.0005)]:
            m,admission,_,_ = wallet.evaluate(events,slip=slip)
            tests[name][label] = dict(metrics=m,admission=admission)
    summary = dict(parent_tp_r=chosen,pick=pick,best_dev_growth=best,eligible=eligible.rule.tolist(),
                   admissions=meta,paired=checks,checks=tests)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(results[results.period.eq('all')].to_string(index=False),flush=True)


if __name__ == '__main__':
    main()
