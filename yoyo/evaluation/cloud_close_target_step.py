"""Move an unfilled 2R profit target to 4R on the next 12H boundary."""
import json

import pandas as pd

from yoyo.evaluation import cloud_close_hourly_trim as hourly
from yoyo.evaluation import cloud_close_fvg_expiry as stress
from yoyo.evaluation import cloud_close_scaleout as split

tested = hourly.tested
EXP = hourly.EXP.parent / 'exp-cloud-close-target-step-20261109-v1'


def build_targets(paths, higher):
    near = paths.loc[paths.tp_r.eq(2)].set_index('event_id', drop=False)
    far = higher.set_index('event_id', drop=False).loc[near.index].copy()
    for col in ('entry_ms','side','symbol','entry_price','stop_price','parent_id','is_control'):
        if not near[col].equals(far[col]):
            raise ValueError(f'Native 2R and 4R legs differ at entry in {col}')
    far['entry_order'] = near.entry_order
    switch = near.entry_ms + 12*tested.w.HOUR
    change = near.exit_bar_ms.ge(switch)
    if (far.loc[change, 'exit_bar_ms'] < switch[change]).any():
        raise ValueError('Cannot activate a previously filled 4R child without an earlier 2R exit')
    step = pd.concat([near.loc[~change], far.loc[change]]).loc[near.index].copy()
    for frame in (step,far):
        frame['tp_r'] = 2  # existing two-thirds child identity in the wallet
    return near,far,step,change


def run(runners, targets, extra_slip=0.):
    data = split.pair_data(runners, targets.reset_index(drop=True), 9)
    if extra_slip:
        return stress._trim_account_with_slippage(data, tested.w.quotes, 1., .2, review_hours=1, market_slip=extra_slip)
    return tested.trim_account(data, tested.w.quotes, 1., .2, review_hours=1)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed stepped-target experiment')
    paths = tested.expiry.trend.thirds.okx_paths()
    tested.expiry.trend.repair.check_parent_sets(paths)
    native = tested.w.read_paths()
    near,far,step,change = build_targets(paths,native[native.tp_r.eq(4)])
    runners = paths[paths.tp_r.eq(0)]
    actual = runners[~runners.is_control]
    base = json.loads((hourly.EXP/'summary.json').read_text())['OKX']['metrics']
    expected = set(pd.read_csv(hourly.EXP/'funded_OKX.csv.gz',usecols=['event_id']).event_id)
    result = {}
    for name,target in [('fixed_2r',near),('fixed_4r',far),('step_2r_to_4r',step)]:
        metrics, parents, equity, trims = run(actual, target.loc[actual.event_id])
        if set(parents.index) != expected or abs(1000 + parents.cash_pnl.sum() - equity.nav.iat[-1]) > .005:
            raise ValueError('Changed funded roster or account fails to reconcile')
        parents.to_csv(EXP / f'funded_{name}.csv.gz')
        equity.to_csv(EXP / f'hourly_{name}.csv.gz', index_label='hour_end_utc')
        result[name] = metrics
    metrics = result['step_2r_to_4r']
    extras = {}
    if all(tested.expiry.trend.second.meets(metrics[k]) and
           metrics[k]['final_usdt'] > result['fixed_2r'][k]['final_usdt'] for k in ('dev','all')):
        controls = runners[runners.is_control & runners.parent_id.isin(actual.parent_id)]
        for name,rows,slip in [('matched',actual[actual.parent_id.isin(controls.parent_id)],0.),
                               ('later',controls,0.),('stress',actual,.0005)]:
            extras[name] = run(rows,step.loc[rows.event_id],slip)[0]
    changed = step[step.index.isin(expected) & change]
    switched_target_fills = changed[changed.exit_reason.str.startswith('take_profit')]
    same_boundary = switched_target_fills.exit_bar_ms.eq(switched_target_fills.entry_ms+12*tested.w.HOUR)
    eligible_gap = same_boundary & switched_target_fills.exit_phase.eq('open')
    audit = dict(funded=len(expected), actual_opportunities=len(actual), funded_alive_at_switch=len(changed),
                 funded_4r_fills=len(switched_target_fills), resting_4r_fills_at_switch_open=int(eligible_gap.sum()))
    summary = dict(audit=audit, unchanged_cancel_after_12h=base, results=result, sensitivities=extras)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary['audit']), flush=True)


if __name__ == '__main__':
    main()
