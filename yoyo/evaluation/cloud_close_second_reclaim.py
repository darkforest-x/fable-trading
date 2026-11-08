"""Fund the first subsequent same-week 12H reclaim from its own native fills."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_drawdown_bounds as drawdown
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_same_time_control as pair
from yoyo.evaluation import cloud_close_scaleout as split
from yoyo.evaluation import cloud_close_thirds as thirds

EXP = wallet.ROOT/'experiments/active/exp-cloud-close-second-reclaim-20261109-v1'
SHARE = 2 / 3


def split_events(paths):
    if (paths.duplicated(['event_id','tp_r']).any() or not paths.tp_r.isin([0,2]).all()
            or not paths.groupby('event_id').size().eq(2).all()):
        raise ValueError('Expected exactly one original runner and 2R path per secondary event')
    if (paths.entry_ms.gt(paths.exit_bar_ms).any() or not paths.exit_phase.isin(['open','intrabar','close']).all()
            or not np.isfinite(paths[['entry_price','exit_price','stop_price','entry_order']]).all().all()):
        raise ValueError('Invalid secondary event execution, risk, or priority')
    fees = np.where(paths.exit_reason.str.startswith('take_profit'),.0002,wallet.TAKER)
    if not np.allclose(fees,paths.exit_fee_rate,atol=1e-12,rtol=0):
        raise ValueError('Native fee class does not match the executed exit')
    if paths.groupby('event_id')[['parent_id','symbol','side','entry_ms','entry_price','entry_order']].nunique().gt(1).any().any():
        raise ValueError('Funded legs disagree on entry identity')
    actual = paths[~paths.is_control]
    control = paths[paths.is_control]
    signal = actual[actual.tp_r.eq(0)].set_index('parent_id')
    null = control[control.tp_r.eq(0)].set_index('parent_id')
    if not signal.index.is_unique or not null.index.is_unique or not null.index.isin(signal.index).all():
        raise ValueError('Control lost its secondary signal parent')
    if not null.entry_ms.gt(signal.loc[null.index,'entry_ms']).all():
        raise ValueError('Later control is not later than the actual secondary event')
    return actual,control


def funded(events, slip=0.):
    data = split.pair_data(events[events.tp_r.eq(0)],events[events.tp_r.eq(2)],9)
    metrics, positions, equity = split.simulate(data,SHARE,slip)
    if not np.isclose(1000 + positions.cash_pnl.sum(),equity.nav.iloc[-1],rtol=1e-8,atol=.005):
        raise ValueError('Secondary account does not reconcile to native orders')
    if not slip:
        unit = pair.returns(events,SHARE).loc[positions.parent_id,'net'].to_numpy()
        if not np.allclose(positions.net_return,unit,rtol=1e-8,atol=1e-10):
            raise ValueError('Dollar-weighted native legs differ from actual entry and exit cash flows')
    audit = dict(opportunities=events.event_id.nunique(),positions=len(positions),unquoted_leg_hours=data['gap_marks'],
                 **data['skips'])
    return metrics,positions,equity,audit


def meets(row):
    return bool(wallet.qualifies(row) and row['payoff_usdt']>=2.5 and row['trades']>=100)


def same_parent_diagnostic(second):
    """Compare two observed entry times in the same week after the second key has occurred."""
    original = thirds.okx_paths()
    first = pair.returns(original[~original.is_control],SHARE)
    later = pair.returns(second,SHARE)
    common = first.join(later,how='inner',lsuffix='_first',rsuffix='_second')
    if not common.entry_second.gt(common.entry_first).all():
        raise ValueError('Second key must be strictly later in every same-parent pair')
    difference = common.net_second-common.net_first
    interval,weeks = pair.uncertainty.draws_by_week(difference,common.entry_first)
    return dict(eligible_at_both_clocks=len(common),signal_weeks=weeks,
                mean_first_pct=100*common.net_first.mean(),mean_second_pct=100*common.net_second.mean(),
                mean_difference_pp=100*difference.mean(),difference_95pct_interval_pp=interval,
                second_improved_pct=100*difference.gt(0).mean(),
                median_wait_hours=(common.entry_second-common.entry_first).median()/wallet.HOUR,
                interpretation='Retrospective same-parent equal-notional diagnostic. The existence of a later key was unknown when the first key closed.')


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Funded second-reclaim experiment already exists')
    paths = pd.read_csv(EXP/'paths.csv.gz',dtype={'symbol':str},keep_default_na=False,float_precision='round_trip')
    real,controls = split_events(paths)
    old = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    first = old[old.venue.eq('OKX') & old.scenario.eq('actual')].drop(columns=['venue','scenario'])
    if set(first.period) != {'all','dev','2025','2026'}:
        raise ValueError('Missing frozen unconditional comparator period')
    # Historical minute marks refined only the whole-period upper bound; keep the
    # original four hourly series comparable in the main table.
    refined = json.loads((drawdown.EXP/'summary.json').read_text())
    if not np.isclose(refined['final_usdt'],first.loc[first.period.eq('all'),'final_usdt'].item()):
        raise ValueError('Frozen first-key account differs from its minute valuation')
    records = first.assign(policy='first',scenario='actual').to_dict('records')
    actual,pos,eq,audit = funded(real)
    records.extend(dict(policy='second',scenario='actual',period=k,**r) for k,r in actual.items())
    pos.to_csv(EXP/'funded.csv.gz')
    eq.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
    checks = {}
    if meets(actual['dev']):
        paired = real[real.parent_id.isin(controls.parent_id)]
        for label,events,slip in [('matched_actual',paired,0.),('matched_control',controls,0.),
                                  ('extra_slippage_5bp',real,.0005)]:
            metrics,_,_,checks[label] = funded(events,slip)
            records.extend(dict(policy='second',scenario=label,period=k,**r) for k,r in metrics.items())
    table = pd.DataFrame(records)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    summary = dict(second_reclaim_account=audit,eligible_parent_pairs=controls.parent_id.nunique(),
                   first_reference_refined_dd_upper_pct=refined['refined_upper_pct'],
                   eligible_dev=meets(actual['dev']),passes_full_risk_and_payoff=meets(actual['all']),
                   reaches_wealth_target=bool(actual['all']['final_usdt']>1_001_000),checks=checks,
                   same_parent_diagnostic=same_parent_diagnostic(real))
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
