"""Close a funded V1.7 trade at the open immediately after a complete failed reclaim."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import repair_io
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as native
from yoyo.evaluation import cloud_close_slots as capacity


EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-reclaim-failure-20261109-v1'


def filter_eligible_paths(paths, hours):
    """Reuse frozen native paths; gate each signal/control at its own entry clock."""
    hours = tuple(int(h) for h in hours)
    if not hours or len(set(hours)) != len(hours) or any(h <= 0 for h in hours):
        raise ValueError('Reclaim thresholds must be unique positive durations')
    required = {'hours','parent_id','event_id','is_control','symbol','side','entry_ms','tp_r'}
    missing = sorted(required - set(paths.columns))
    if missing:
        raise ValueError(f'Frozen native reclaim paths missing columns: {missing}')
    if paths.duplicated(['hours','event_id','tp_r']).any():
        raise ValueError('Frozen native reclaim paths contain duplicate duration/event/leg keys')
    if not set(paths.hours.astype(int)).issubset(set(hours)):
        raise ValueError('Frozen reclaim paths contain an unregistered duration')

    identity = ['parent_id','is_control','symbol','side','entry_ms']
    if paths.groupby('event_id')[identity].nunique(dropna=False).gt(1).any().any():
        raise ValueError('An event identity maps to inconsistent parent/time/direction metadata')
    events = paths.drop_duplicates('event_id').copy()
    actual = events[~events.is_control]
    controls = events[events.is_control]
    if (actual.event_id.duplicated().any() or actual.parent_id.duplicated().any()
            or controls.event_id.duplicated().any() or controls.parent_id.duplicated().any()):
        raise ValueError('Frozen native signal/control events must be unique per parent')

    rank = leader.leadership().dropna()
    event_clock = pd.MultiIndex.from_frame(events[['entry_ms','symbol']])
    events['eligible_90d'] = event_clock.isin(rank.index)
    eligible_actual = events[~events.is_control & events.eligible_90d]
    parent_ids = set(eligible_actual.parent_id)
    rank_nonnull_controls = events[events.is_control & events.eligible_90d]
    eligible_controls = rank_nonnull_controls[rank_nonnull_controls.parent_id.isin(parent_ids)]
    keep_events = set(eligible_actual.event_id) | set(eligible_controls.event_id)
    selected = paths[paths.event_id.isin(keep_events)].copy()
    if selected.duplicated(['hours','event_id','tp_r']).any():
        raise ValueError('Eligibility filtering introduced duplicate native legs')
    leg_sets = selected.groupby(['event_id','hours']).tp_r.agg(lambda x: set(x.astype(float)))
    if not leg_sets.map(lambda x: x == {0.0,2.0}).all():
        raise ValueError('Each event/duration must retain exactly one native runner and 2R leg')
    event_hours = selected.drop_duplicates(['event_id','hours']).groupby('event_id').hours.apply(
        lambda x: set(x.astype(int)))
    if not event_hours.map(lambda x: x == set(hours)).all():
        raise ValueError('Each eligible event must retain every configured reclaim duration')
    if len(selected) != selected.event_id.nunique() * len(hours) * 2:
        raise ValueError('Native reclaim event/duration/two-leg row count does not reconcile')
    selected_controls = selected[selected.is_control]
    selected_actual = selected[~selected.is_control]
    if not selected_controls.parent_id.isin(selected_actual.parent_id).all():
        raise ValueError('A selected control has no 90-day-eligible actual parent')
    audit = {
        'source_events': int(len(events)),
        'source_actual_events': int((~events.is_control).sum()),
        'source_control_events': int(events.is_control.sum()),
        'rank_nonnull_actual_events': int(eligible_actual.event_id.nunique()),
        'rank_nonnull_control_events': int(rank_nonnull_controls.event_id.nunique()),
        'rank_nonnull_controls_with_eligible_actual_parent': int(eligible_controls.event_id.nunique()),
        'rank_nonnull_control_orphans_removed': int(
            rank_nonnull_controls.event_id.nunique() - eligible_controls.event_id.nunique()),
        'selected_events': int(selected.event_id.nunique()),
        'selected_actual_events': int(selected_actual.event_id.nunique()),
        'selected_control_events': int(selected_controls.event_id.nunique()),
        'selected_parent_pairs': int(len(set(selected_actual.parent_id) & set(selected_controls.parent_id))),
        'durations': list(hours), 'legs_per_event_duration': 2,
        'selected_rows': int(len(selected)), 'duplicate_event_hour_leg_keys': 0,
        'complete_duration_and_leg_sets': True,
    }
    return selected, audit


def generate(hours):
    paths = wallet.read_paths()
    rank = leader.leadership().dropna().index
    paths = paths[paths.tp_r.eq(0) & pd.MultiIndex.from_frame(paths[['entry_ms','symbol']]).isin(rank)].copy()
    original = paths[~paths.is_control].set_index('parent_id')
    paths = paths[paths.parent_id.isin(original.index)].copy()
    source = pd.read_csv(wallet.SOURCE/'signals.csv', usecols=['prior_atr_pct','reference_entry'],
                         float_precision='round_trip')
    source.index = pd.RangeIndex(1, len(source)+1)
    reference = original.source_row_number.map(source.reference_entry)
    if not ((reference - original.stop_price) * original.side).gt(0.).all():
        raise ValueError('Source touched levels do not lie inside original protective stops')
    paths['reclaim_level'] = paths.entry_price * paths.parent_id.map(reference / original.entry_price)
    paths.loc[~paths.is_control,'reclaim_level'] = paths.loc[~paths.is_control,'parent_id'].map(reference)
    paths['entry_order'] = paths.source_row_number.map(source.prior_atr_pct).fillna(np.inf)
    paths['risk_distance'] = (paths.entry_price-paths.stop_price)*paths.side
    paths['week'] = ''
    pieces = []
    for symbol, group in paths.groupby('symbol'):
        folder = wallet.SOURCE/'inputs'/symbol
        if not (folder/'bars1h.csv.gz').is_file():
            folder = wallet.TOP/'inputs'/symbol
        twelve = native._load_bars(folder/'bars12h.csv.gz','12h')
        hourly = native._load_bars(folder/'bars1h.csv.gz','1h')
        q, clock, _ = native._prepare_symbol(twelve,hourly)
        starts, ends = clock.searchsorted(group.entry_ms), clock.searchsorted(group.exit_bar_ms)
        if not np.array_equal(clock[starts],group.entry_ms) or not np.array_equal(clock[ends],group.exit_bar_ms):
            raise ValueError(f'Original position boundary missing on {symbol}')
        group = group.assign(entry_i=starts,end_i=ends)
        rows = group.sort_values(['entry_i','event_id']).to_dict('records')
        for duration in hours:
            filled, _ = native._simulate_symbol(rows,q,clock,(0.,2.),8,reclaim_hours=duration)
            part = pd.DataFrame(filled)
            part['entry_order'] = part.event_id.map(group.set_index('event_id').entry_order)
            part['hours'] = duration
            pieces.append(part[['hours','parent_id','event_id','is_control','symbol','side','entry_ms',
                                'exit_bar_ms','exit_phase','exit_reason','entry_price','exit_price','exit_fee_rate',
                                'stop_price','tp_r','entry_order']])
    result = pd.concat(pieces,ignore_index=True)
    if len(result) != len(paths)*2*len(hours) or result.duplicated(['hours','event_id','tp_r']).any():
        raise ValueError('Incomplete or duplicate protected paths')
    return result


def main(output_dir=None):
    output_dir = repair_io.new_output_dir(
        output_dir, root=wallet.ROOT, historical_dir=EXP,
        artifacts=('paths.csv.gz','account_metrics.csv','summary.json',
                   'funded_dev_pick.csv.gz','hourly_dev_pick.csv.gz'), description=__doc__)
    cfg = json.loads((EXP/'config.json').read_text())
    hours = cfg['hours']
    # Reuse the archived native-generated v1 legs and their event clocks. Do
    # not re-price the history; only correct the 90d eligibility cohort here.
    source_paths = EXP/'paths.csv.gz'
    if not source_paths.is_file():
        raise FileNotFoundError(f'Required frozen native reclaim paths are missing: {source_paths}')
    raw_paths = pd.read_csv(source_paths, dtype={'symbol':str}, keep_default_na=False,
                            float_precision='round_trip')
    paths, path_audit = filter_eligible_paths(raw_paths, hours)
    baseline = pd.read_csv(leader.EXP/'account_metrics.csv')
    rows = baseline[baseline.top_fraction.eq(1)].drop(columns='top_fraction').assign(hours=0).to_dict('records')
    curves = {}
    for duration in hours:
        pool = paths[paths.hours.eq(duration) & ~paths.is_control]
        metrics, trades, equity = capacity.run(pool,9,.75)
        rows.extend(dict(hours=duration,period=p,**m) for p,m in metrics.items())
        curves[duration] = trades,equity
        gc.collect()
    table = pd.DataFrame(rows)
    dev = table[table.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible = dev[dev.apply(wallet.qualifies,axis=1) & dev.payoff_usdt.ge(2.5) & dev.trades.ge(100)]
    pick = int(eligible.hours.iloc[0]) if len(eligible) else None
    checks = {}
    if pick in hours:
        trades, equity = curves[pick]
        trades.to_csv(output_dir/'funded_dev_pick.csv.gz')
        equity.to_csv(output_dir/'hourly_dev_pick.csv.gz',index_label='hour_end_utc')
        chosen = paths[paths.hours.eq(pick)]
        real, control = chosen[~chosen.is_control], chosen[chosen.is_control]
        matched = real[real.parent_id.isin(control.parent_id)]
        for key,pool,slip in [('paired_signals',matched,0.),('control',control,0.),('slippage_5bp',real,.0005)]:
            checks[key] = capacity.run(pool,9,.75,slip)[0]
    counts = paths[~paths.is_control & paths.tp_r.eq(0) & paths.exit_reason.eq('reclaim_failed_at_close')]
    summary = dict(experiment_id=cfg['id'],
                   output_dir=str(output_dir), pick=pick, checks=checks,
                   failures=counts.hours.value_counts().to_dict(), path_audit=path_audit,
                   reused_native_source=str(source_paths.relative_to(wallet.ROOT)))
    paths.to_csv(output_dir/'paths.csv.gz',index=False,compression='gzip')
    table.to_csv(output_dir/'account_metrics.csv',index=False)
    (output_dir/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))
    return dict(output_dir=str(output_dir), account_metrics=table, summary=summary, paths=paths)


if __name__=='__main__':
    main()
