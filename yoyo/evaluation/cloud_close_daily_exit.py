"""Keep the 12H winner uncapped when the latest complete daily cloud points the same way."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_okx_agreement as agreement
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_side_exit as asymmetric
from yoyo.evaluation import cloud_close_thirds as thirds


DAY = 24 * wallet.HOUR
EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-daily-exit-20261109-v1'


def daily_sides(bars):
    """UTC daily close from both consecutive 12H observations, with 50-day warmup after any gap."""
    if bars.time.duplicated().any() or not bars.time.is_monotonic_increasing or bars.time.mod(DAY//2).any():
        raise ValueError('Non-unique or off-clock 12H input')
    day = bars.time // DAY * DAY
    groups = bars.groupby(day).close
    daily = groups.last()[groups.size().eq(2)].to_frame('close')
    point = agreement._ema_points(daily,DAY)
    result = np.sign(point.ema20-point.ema50).where(point.ready)
    result.index = result.index+DAY
    return result


def attach_context(paths):
    key = paths[paths.tp_r.eq(0)].set_index('event_id').copy()
    key['daily_side'] = np.nan
    for symbol,g in key.groupby('symbol'):
        path = wallet.SOURCE/'inputs'/symbol/'bars12h.csv.gz'
        if not path.is_file():
            path = wallet.TOP/'inputs'/symbol/'bars12h.csv.gz'
        bars = pd.read_csv(path,usecols=['time','close'],float_precision='round_trip')
        side = daily_sides(bars)
        observed_at = g.entry_ms//DAY*DAY
        key.loc[g.index,'daily_side'] = side.reindex(observed_at).to_numpy()
    key['keep_full'] = key.side.eq(key.daily_side)
    return key[['parent_id','symbol','entry_ms','side','is_control','daily_side','keep_full']]


def main():
    paths = thirds.okx_paths()
    context = attach_context(paths)
    context.to_csv(EXP/'daily_context.csv.gz')
    partials = set(context.index[~context.keep_full])
    real = paths[~paths.is_control]
    controls = paths[paths.is_control & paths.parent_id.isin(real.parent_id)]
    baseline = pd.read_csv(asymmetric.EXP/'account_metrics.csv')
    rows = baseline[baseline.policy.isin(['both','neither'])].to_dict('records')
    metrics, trades, equity = asymmetric.account(real,partial_events=partials)
    roster = pd.read_csv(leader.EXP/'funded_1.0.csv.gz',usecols=['event_id'])
    if set(roster.event_id)!=set(trades.index) or len(roster)!=len(trades):
        raise ValueError('Daily exit attribution changed original admissions')
    rows.extend(dict(policy='daily_aligned_runner',period=p,**m) for p,m in metrics.items())
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    trades.to_csv(EXP/'funded.csv.gz')
    equity.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
    dev = metrics['dev']
    passes = wallet.qualifies(dev) and dev['payoff_usdt']>=2.5 and dev['trades']>=100
    checks = {}
    if passes:
        for label,pool,slip in [('paired',real[real.parent_id.isin(controls.parent_id)],0.),
                                ('control',controls,0.),('stress',real,.0005)]:
            checks[label] = asymmetric.account(pool,slip=slip,partial_events=partials)[0]
    actual = context[~context.is_control]
    result = dict(eligible=len(actual),known_daily_direction=int(actual.daily_side.notna().sum()),
                  aligned_full_runner=int(actual.keep_full.sum()),
                  funded_full_runner=int(context.loc[trades.index,'keep_full'].sum()),dev_pass=bool(passes),checks=checks)
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__ == '__main__':
    main()
