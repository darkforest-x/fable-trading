"""Test whether V1.7 signal-key H1 closes accepted the touched Monday 1.618.

The signal remains the frozen original 12H V1.7 event. The feature reads only
the final three complete H1 closes ending at its 12H confirmation boundary.
Orders, fills, four-child exposure, volume caps, fees and NAV remain in the
existing VectorBT-backed cloud_close_liquid_queue account.
"""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_liquid_queue as queue
from yoyo.evaluation import cloud_close_reward_paths as native
from yoyo.monitor.cloud_signal_plans import stop_pct_for


ROOT = queue.repair.wallet.ROOT
EXP = ROOT / 'experiments/active/exp-cloud-close-reclaim-acceptance-20261109-v1'
CONFIG_PATH = EXP / 'config.json'
BASE = queue.EXP / 'balanced'
HOUR = queue.repair.wallet.HOUR
STEP = 12 * HOUR
POLICIES = ('all_observable', 'stable_3', 'last_only_1')


def _load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def _validate_config(config: dict) -> None:
    if config.get('experiment_id') != EXP.name:
        raise ValueError('Unexpected experiment id')
    pool = config['input_pool']
    expected_sources = {
        'source_experiment': queue.EXP.name,
        'candidate_audit': 'experiments/active/exp-cloud-close-breakout-historical-20261109-v1/candidate_audit_full_360.csv.gz',
        'signals': str(queue.repair.wallet.SOURCE.relative_to(ROOT) / 'signals.csv'),
        'prior_turnover': str((queue.history.EXP / 'opportunities.csv.gz').relative_to(ROOT)),
        'baseline_summary': str((queue.EXP / 'summary.json').relative_to(ROOT)),
    }
    for key, expected in expected_sources.items():
        if pool.get(key) != expected:
            raise ValueError(f'Frozen input {key} differs from local source: {pool.get(key)!r} != {expected!r}')
    if (int(pool['historical_contract_sources']) != 360
            or int(pool['actual_signal_opportunities_expected']) != 12442
            or int(pool['positive_turnover_opportunities_expected']) != 12284):
        raise ValueError('Frozen event/source counts changed')
    account = config['account']
    if int(account['slots']) != 9 or float(account['entry_cap_fraction']) != .1:
        raise ValueError('Frozen nine-slot and 10% turnover-cap rules changed')
    if tuple(config['outputs']['conditional_account_dirs']) != ('stable_3/', 'last_only_1/'):
        raise ValueError('Frozen account policies changed')


def _classify(closes: np.ndarray, side: int, level: float) -> tuple[str, list[bool], list[bool]]:
    """Strictly classify three completed closes against the frozen touched edge."""
    values = np.asarray(closes, dtype=float)
    if values.shape != (3,) or not np.isfinite(values).all():
        return 'missing_last3', [], []
    signed = side * (values - level)
    inside = (signed > 0).tolist()
    outside = (signed < 0).tolist()
    if not inside[-1]:
        return ('final_at_boundary' if signed[-1] == 0 else 'final_outside'), inside, outside
    if all(inside):
        return 'stable_3', inside, outside
    if outside[0] and outside[1]:
        return 'last_only_1', inside, outside
    if outside[0] and inside[1]:
        return 'two_consecutive', inside, outside
    return 'other_observed', inside, outside


def _candidate_anchors(path: Path) -> tuple[pd.DataFrame, dict]:
    """Read the independent 360-name reclaim candidate geometry for cross-checks."""
    if not path.is_file():
        raise FileNotFoundError(f'Frozen 360-name reclaim candidates missing: {path}')
    use = ['event_id', 'symbol', 'side', 'key_open_ms', 'confirmation_ms',
           'entry_ms', 'reference_anchor', 'entry_status', 'arm', 'is_control']
    raw = pd.read_csv(path, usecols=use, dtype={'symbol': str},
                      float_precision='round_trip')
    raw = raw[(raw.arm == 'reclaim') & (~raw.is_control.astype(bool))].copy()
    raw['entry_ms'] = pd.to_numeric(raw.entry_ms, errors='coerce')
    raw['entry_ms'] = raw.entry_ms.round().astype('Int64')
    raw['side'] = pd.to_numeric(raw.side, errors='raise').astype('int8')
    key = ['symbol', 'side', 'entry_ms']
    raw = raw[raw.entry_ms.notna()]
    if raw.duplicated(key).any():
        raise ValueError('Candidate audit has duplicate reclaim signal clock keys')
    index = raw.set_index(key)
    return index, {
        'source': str(path.relative_to(ROOT)),
        'reclaim_actual_candidates': int(len(raw)),
        'symbols': int(raw.symbol.nunique()),
        'unique_clock_keys': int(len(index)),
    }


def _frozen_signal_anchors(path: Path) -> tuple[pd.Series, dict]:
    """Load 1-based original signal-row references where the frozen file has them."""
    if not path.is_file():
        return pd.Series(dtype=float), {'source': str(path.relative_to(ROOT)), 'rows': 0,
                                        'available': False}
    frame = pd.read_csv(path, usecols=['reference_entry'], float_precision='round_trip')
    frame.index = pd.RangeIndex(1, len(frame) + 1, name='source_row_number')
    return frame.reference_entry.astype(float), {
        'source': str(path.relative_to(ROOT)), 'rows': int(len(frame)), 'available': True,
    }


def _range_level(times: np.ndarray, ohlc: np.ndarray, key_open_ms: int,
                 side: int) -> tuple[float | None, str, dict]:
    """Rebuild the exact NY-Monday 24-H1 1.618 range used by native V1.7."""
    window = native._ny_week_window(int(key_open_ms))
    expected = np.arange(int(window['start']), int(window['monday_end']), HOUR,
                         dtype=np.int64)
    first = int(np.searchsorted(times, expected[0]))
    found = times[first:first + len(expected)]
    if len(found) != 24 or not np.array_equal(found, expected):
        return None, 'monday_h1_range_incomplete', window
    monday = ohlc[first:first + len(expected)]
    if (not np.isfinite(monday).all() or np.any(monday <= 0)
            or np.any(monday[:, 2] > monday[:, [0, 1, 3]].min(axis=1))
            or np.any(monday[:, 1] < monday[:, [0, 2, 3]].max(axis=1))):
        return None, 'monday_h1_range_invalid', window
    high, low = float(monday[:, 1].max()), float(monday[:, 2].min())
    if not high > low:
        return None, 'monday_h1_range_empty', window
    lower, upper = low - .618 * (high - low), high + .618 * (high - low)
    return (lower if side > 0 else upper), 'ready', {
        **window, 'monday_high': high, 'monday_low': low,
        'monday_lower_1618': lower, 'monday_upper_1618': upper,
    }


def _read_positive_turnover_ids(path: Path) -> tuple[set[str], pd.Series, dict]:
    volume = pd.read_csv(path, usecols=['event_id', 'prior_turnover_usdt'],
                         dtype={'event_id': str}, float_precision='round_trip')
    if volume.event_id.duplicated().any():
        raise ValueError('Prior-turnover input contains duplicate V1.7 event IDs')
    turnover = volume.set_index('event_id').prior_turnover_usdt
    positive = turnover.div(600.0).ge(1e-6) & turnover.notna()
    return set(turnover.index[positive]), turnover, {
        'source': str(path.relative_to(ROOT)), 'events': int(len(volume)),
        'positive_turnover_events': int(positive.sum()),
        'missing_or_zero_events': int((~positive).sum()),
        'cap_rule': 'prior native H1 quote turnover / 60 minutes; must be >= 1e-6 USDT per minute',
    }


def build_features(config: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Build one causal H1 acceptance row per frozen real event, preserving gaps."""
    config = config or _load_config()
    _validate_config(config)
    rows, quotes, sources, repair_meta = queue.repair.pool()
    events = rows[rows.tp_r.eq(0)].copy()
    if len(events) != 12442 or events.event_id.nunique() != 12442:
        raise ValueError(f'Frozen 360 actual event set changed: {len(events)}')
    if events.is_control.astype(bool).any():
        raise ValueError('Queue source unexpectedly includes controls in the actual roster')
    if events.symbol.nunique() != 331 or len(sources) != 360:
        raise ValueError(f'Frozen historical symbol source changed: events={events.symbol.nunique()}, sources={len(sources)}')
    if events[['entry_ms', 'key_open_ms', 'entry_price', 'stop_price']].isna().any().any():
        raise ValueError('Frozen event clock or stop geometry is incomplete')
    if not events.event_id.is_unique:
        raise ValueError('Each actual event must occur once at tp_r=0')

    candidate_path = ROOT / config['input_pool']['candidate_audit']
    signal_path = ROOT / config['input_pool']['signals']
    turnover_path = ROOT / config['input_pool']['prior_turnover']
    candidate_index, candidate_audit = _candidate_anchors(candidate_path)
    signal_anchors, signal_audit = _frozen_signal_anchors(signal_path)
    positive_ids, turnover, turnover_audit = _read_positive_turnover_ids(turnover_path)
    unknown_turnover = set(events.event_id) - set(turnover.index)
    if unknown_turnover:
        raise ValueError(f'Turnover source misses {len(unknown_turnover)} frozen events')

    records = []
    symbols = []
    for symbol, group in events.groupby('symbol', sort=True):
        time, ohlc = quotes(symbol)
        time = np.asarray(time, dtype=np.int64)
        ohlc = np.asarray(ohlc, dtype=float)
        if (not len(time) or not np.all(np.diff(time) > 0)
                or not np.equal(time % HOUR, 0).all() or ohlc.shape != (len(time), 4)):
            raise ValueError(f'Invalid frozen H1 quotes for {symbol}')
        source_paths = sources[symbol]
        source_h1 = str(source_paths[0].relative_to(ROOT))
        source_h12 = str(source_paths[1].relative_to(ROOT)) if source_paths[1] is not None else None
        status_counts = {}
        for event in group.itertuples(index=False):
            entry_ms = int(event.entry_ms)
            key_open_ms = int(event.key_open_ms)
            side = int(event.side)
            if (entry_ms != key_open_ms + STEP or key_open_ms % STEP != 0
                    or entry_ms % STEP != 0):
                raise ValueError(f'Not a confirmed UTC12H key / next H1 open: {event.event_id}')
            window = native._ny_week_window(key_open_ms)
            if (key_open_ms < int(window['monday_end'])
                    or entry_ms > int(window['expires'])):
                raise ValueError(f'V1.7 key escaped its complete NY week: {event.event_id}')

            # Preserve the path's fixed stop-derived signal anchor. Rebuild the
            # Monday range independently from the 24 completed H1 bars and
            # cross-check against the archived 360-name V1.7 candidate.
            stop_pct = stop_pct_for(symbol)
            frozen_anchor = float(event.stop_price) / (1.0 - side * stop_pct)
            if not np.isfinite(frozen_anchor) or frozen_anchor <= 0:
                raise ValueError(f'Invalid frozen V1.7 1.618 anchor: {event.event_id}')
            if int(event.source_row_number) in signal_anchors.index:
                original_anchor = float(signal_anchors.loc[int(event.source_row_number)])
                if not np.isclose(original_anchor, frozen_anchor, rtol=1e-9, atol=1e-12):
                    raise ValueError(f'Signal-row reference differs from native stop: {event.event_id}')
                anchor_source = 'frozen_signals_csv_reference_entry'
            else:
                original_anchor = frozen_anchor
                anchor_source = 'native_path_stop_inversion'

            candidate_key = (str(symbol), side, entry_ms)
            archived_anchor = np.nan
            archived_event_id = ''
            if candidate_key in candidate_index.index:
                candidate = candidate_index.loc[candidate_key]
                archived_anchor = float(candidate.reference_anchor)
                archived_event_id = str(candidate.event_id)
                if not np.isclose(archived_anchor, original_anchor, rtol=1e-9, atol=1e-12):
                    raise ValueError(f'Frozen path anchor differs from 360 candidate: {event.event_id}')

            range_anchor, range_state, range_info = _range_level(time, ohlc, key_open_ms, side)
            range_matches = (range_state == 'ready' and
                             np.isclose(range_anchor, original_anchor, rtol=1e-9, atol=1e-12))
            expected = np.array([entry_ms - 3*HOUR, entry_ms - 2*HOUR,
                                 entry_ms - HOUR], dtype=np.int64)
            first = int(np.searchsorted(time, expected[0]))
            actual_times = time[first:first + 3]
            complete = len(actual_times) == 3 and np.array_equal(actual_times, expected)
            entry_i = int(np.searchsorted(time, entry_ms))
            has_next_open = entry_i < len(time) and int(time[entry_i]) == entry_ms
            entry_open_matches = (has_next_open and np.isclose(
                float(ohlc[entry_i, 0]), float(event.entry_price), rtol=1e-9, atol=1e-12))
            closes = ohlc[first:first+3, 3].copy() if complete else np.full(3, np.nan)
            if complete and range_state == 'ready':
                gate, inside, outside = _classify(closes, side, original_anchor)
            elif not complete:
                gate, inside, outside = 'missing_last3', [], []
            else:
                gate, inside, outside = range_state, [], []
            final_close_inside = bool(inside[-1]) if inside else False
            if gate in {'stable_3', 'last_only_1', 'two_consecutive', 'other_observed'}:
                window_status = 'eligible'
            else:
                window_status = gate

            status_counts[window_status] = status_counts.get(window_status, 0) + 1
            records.append({
                'event_id': str(event.event_id), 'parent_id': str(event.parent_id),
                'source_row_number': int(event.source_row_number), 'symbol': str(symbol),
                'side': side, 'key_open_ms': key_open_ms, 'confirmation_ms': entry_ms,
                'entry_ms': entry_ms, 'entry_price': float(event.entry_price),
                'stop_price': float(event.stop_price), 'reference_1618': original_anchor,
                'reference_source': anchor_source,
                'candidate_audit_event_id': archived_event_id,
                'candidate_audit_anchor': archived_anchor,
                'candidate_anchor_crosschecked': bool(np.isfinite(archived_anchor)),
                'monday_range_state': range_state,
                'monday_range_anchor': range_anchor,
                'monday_range_matches_frozen_reference': bool(range_matches),
                'ny_week': str(window['week']),
                'h1_before_entry_complete': bool(complete),
                'h1_last3_open_ms': '|'.join(str(int(x)) for x in expected) if complete else '',
                'h1_close_tminus3h': float(closes[0]) if complete else np.nan,
                'h1_close_tminus2h': float(closes[1]) if complete else np.nan,
                'h1_close_tminus1h': float(closes[2]) if complete else np.nan,
                'h1_inside_bits_oldest_to_newest': ''.join('1' if x else '0' for x in inside),
                'h1_outside_bits_oldest_to_newest': ''.join('1' if x else '0' for x in outside),
                'final_h1_close_inside': final_close_inside,
                'acceptance_state': gate,
                'observable_status': window_status,
                'positive_turnover_cap_eligible': str(event.event_id) in positive_ids,
                'prior_turnover_usdt': float(turnover.loc[event.event_id])
                    if pd.notna(turnover.loc[event.event_id]) else np.nan,
                'entry_open_present': bool(has_next_open),
                'entry_open_matches_frozen_path': bool(entry_open_matches),
                'source_h1_path': source_h1, 'source_h12_path': source_h12,
                'source_available_end_ms': int(source_paths[2]),
            })
        symbols.append({
            'symbol': str(symbol), 'events': int(len(group)),
            'source_h1_path': source_h1, 'source_h12_path': source_h12,
            'source_available_end_ms': int(source_paths[2]),
            'acceptance_states': status_counts,
        })
        print(f'feature {symbol}: {len(group)} events, {status_counts}', flush=True)

    features = pd.DataFrame(records).sort_values(['entry_ms', 'symbol', 'event_id'], kind='stable')
    if len(features) != len(events) or features.event_id.duplicated().any():
        raise ValueError('Feature table does not contain exactly one row per actual event')
    if not features.entry_open_matches_frozen_path.all():
        bad = features.loc[~features.entry_open_matches_frozen_path, 'event_id'].head(5).tolist()
        raise ValueError(f'Next-H1 open does not match frozen entry for {bad}')
    if not features.monday_range_matches_frozen_reference.all():
        bad = features.loc[~features.monday_range_matches_frozen_reference,
                           ['event_id', 'monday_range_state', 'monday_range_anchor',
                            'reference_1618']].head(10).to_dict('records')
        raise ValueError(f'Current 24-H1 Monday range differs from frozen V1.7 reference: {bad}')
    if not features.final_h1_close_inside.all():
        bad = features.loc[~features.final_h1_close_inside,
                           ['event_id', 'h1_close_tminus1h', 'reference_1618']].head(10).to_dict('records')
        raise ValueError(f'Original V1.7 12H close is not reconstructed by its final H1 close: {bad}')
    audit = {
        'event_rows': int(len(features)), 'actual_events': int(len(events)),
        'control_rows': int(events.is_control.astype(bool).sum()),
        'symbols_with_events': int(events.symbol.nunique()),
        'historical_source_symbols': int(len(sources)),
        'positive_turnover_events': int(features.positive_turnover_cap_eligible.sum()),
        'nonpositive_or_missing_turnover_events': int((~features.positive_turnover_cap_eligible).sum()),
        'acceptance_states': features.acceptance_state.value_counts(dropna=False).to_dict(),
        'observable_states': features.observable_status.value_counts(dropna=False).to_dict(),
        'all_last3_h1_windows_complete': bool(features.h1_before_entry_complete.all()),
        'all_next_h1_open_matches_frozen_path': bool(features.entry_open_matches_frozen_path.all()),
        'all_final_h1_closes_reconstruct_original_v17_close': bool(features.final_h1_close_inside.all()),
        'candidate_anchor_matches': int(features.candidate_anchor_crosschecked.sum()),
        'candidate_anchor_fallbacks': int((~features.candidate_anchor_crosschecked).sum()),
        'all_monday_ranges_match_frozen_reference': bool(features.monday_range_matches_frozen_reference.all()),
        'all_monday_range_checks': int(features.monday_range_matches_frozen_reference.sum()),
        'repair_source_metadata': repair_meta,
        'candidate_source_metadata': candidate_audit,
        'signal_anchor_source_metadata': signal_audit,
        'turnover_source_metadata': turnover_audit,
        'source_by_symbol': symbols,
        'causality': {
            'decision_ms': 'entry_ms == confirmed native UTC 12H close; original V1.7 signal is already finalized at this boundary',
            'feature_h1_open_ms': 'entry_ms - 3h, entry_ms - 2h, entry_ms - 1h exactly; each close occurs no later than entry_ms',
            'entry_price_clock': 'H1 open at entry_ms, cross-checked against frozen path entry_price; this row is excluded from H1 close features',
            'no_future_data': True,
        },
    }
    return features, rows, audit


def _existing_baseline(config: dict) -> dict:
    path = ROOT / config['input_pool']['baseline_summary']
    data = json.loads(path.read_text())
    balanced = data['arms']['balanced']
    if (int(data['eligible']) != 12442
            or int(data['liquid_eligible']) != 12284
            or int(balanced['signals']) != 1593):
        raise ValueError('Frozen balanced baseline roster or capacity changed')
    metrics = balanced['cases']['fees']['metrics']['all']
    if not np.isclose(float(metrics['final_usdt']), 51957.209636755935,
                      rtol=1e-10, atol=1e-6):
        raise ValueError('Frozen balanced baseline final cash changed')
    return {'source_summary': str(path.relative_to(ROOT)), 'source_arm': 'balanced',
            'metrics': balanced['cases'], 'eligible_count': int(data['eligible']),
            'liquid_eligible': int(data['liquid_eligible']),
            'funded_positions': int(balanced['signals']),
            'funded_paths': str((BASE / 'selected_paths.csv.gz').relative_to(ROOT)),
            'hourly_nav': str((BASE / 'hourly_fees.csv.gz').relative_to(ROOT))}


def _metrics_rows(policy: str, cases: dict, output_rows: list[dict]) -> None:
    for case_name in ('fees', 'stress'):
        case = cases.get(case_name)
        if not case:
            continue
        for period, values in case['metrics'].items():
            output_rows.append({'policy': policy, 'case': case_name,
                                'period': period, **values})


def _run_account(policy: str, event_ids: set[str]) -> dict:
    """Run only the changed candidate cohort through the existing VectorBT wallet."""
    if not event_ids:
        return {'status': 'empty_gate', 'eligible_event_ids': 0}
    run_dir = EXP / policy
    run_dir.mkdir(parents=True, exist_ok=True)
    queue.main(experiment=run_dir, policies=[('balanced', 'balanced')],
               eligible_by_arm={'balanced': event_ids})
    summary_path = run_dir / 'summary.json'
    data = json.loads(summary_path.read_text())
    arm = data['arms']['balanced']
    if int(data['eligible']) != 12442 or int(data['liquid_eligible']) != 12284:
        raise ValueError(f'{policy}: frozen unfiltered queue counts changed')
    selected_path = run_dir / 'balanced' / 'selected_paths.csv.gz'
    if not selected_path.is_file():
        raise ValueError(f'{policy}: missing VectorBT selected-path receipt')
    funded_ids = set(pd.read_csv(selected_path, usecols=['event_id'], dtype={'event_id': str}).event_id)
    if not funded_ids.issubset(event_ids):
        raise ValueError(f'{policy}: a funded position was outside its preregistered H1 gate')
    return {'status': 'completed', 'eligible_event_ids': len(event_ids),
            'account_summary': str(summary_path.relative_to(ROOT)), 'arm': arm}


def mini_check() -> dict:
    """Deterministic policy and time-boundary check without any fill accounting."""
    level, side = 100.0, 1
    cases = {
        'stable_3': np.array([100.1, 101., 102.]),
        'last_only_1': np.array([99., 98., 100.1]),
        'two_consecutive': np.array([99., 100.1, 101.]),
        'other_observed': np.array([100.1, 99., 100.1]),
    }
    observed = {name: _classify(values, side, level)[0] for name, values in cases.items()}
    expected = {name: name for name in cases}
    if observed != expected:
        raise ValueError(f'Synthetic acceptance groups differ: {observed}')
    clock = np.arange(0, 5 * HOUR, HOUR, dtype=np.int64)
    decision = int(clock[-1])
    expected_feature_opens = np.array([decision - 3*HOUR,
                                       decision - 2*HOUR,
                                       decision - HOUR])
    # The decision/open row is deliberately not part of completed H1 closes.
    if np.any(expected_feature_opens >= decision) or expected_feature_opens[-1] + HOUR != decision:
        raise ValueError('Feature includes the next H1 execution open or skips the decision close')
    return {'synthetic_policy_states': observed,
            'feature_opens_ms_relative_to_decision': [-3*HOUR, -2*HOUR, -HOUR],
            'next_open_excluded': True}


def run() -> dict:
    if (EXP / 'summary.json').exists():
        raise FileExistsError('Refusing to overwrite a completed reclaim-acceptance experiment')
    cfg = json.loads((EXP / 'config.json').read_text())
    if cfg['experiment_id'] != EXP.name:
        raise ValueError('Unexpected frozen experiment id')
    _validate_config(cfg)
    features, rows, audit = build_features(cfg)
    baseline = _existing_baseline(cfg)
    positive_ids = set(features.loc[features.positive_turnover_cap_eligible, 'event_id'])
    if len(positive_ids) != 12284:
        raise ValueError(f'Observable control changed from 12,284 events: {len(positive_ids)}')

    # Verify the no-change policy exactly reproduces the existing 12,284-entry
    # balanced applicant pool; the funded roster is deliberately smaller.
    turnover_path = ROOT / cfg['input_pool']['prior_turnover']
    positive_ref = pd.read_csv(turnover_path, usecols=['event_id', 'prior_turnover_usdt'],
                               dtype={'event_id': str}, float_precision='round_trip')
    positive_ref = set(positive_ref.loc[positive_ref.prior_turnover_usdt.div(600).ge(1e-6), 'event_id'])
    if positive_ref != positive_ids:
        raise ValueError('Rebuilt observable applicant cohort differs from the frozen queue inputs')

    EXP.mkdir(parents=True, exist_ok=True)
    features.to_csv(EXP / cfg['outputs']['confirmation_features'], index=False, compression='gzip')
    pd.DataFrame(audit['source_by_symbol']).to_csv(EXP / cfg['outputs']['source_coverage'], index=False)
    _safe_json(EXP / cfg['outputs']['feature_audit'], audit)

    metric_rows: list[dict] = []
    _metrics_rows('all_observable', baseline['metrics'], metric_rows)
    results = {
        'all_observable': {
            'status': 'reused_frozen_no_change_control',
            'eligible_event_ids': 12284,
            'input_opportunities': 12442,
            'baseline': baseline,
        }
    }
    for policy in ('stable_3', 'last_only_1'):
        eligible = set(features.loc[
            features.acceptance_state.eq(policy) & features.positive_turnover_cap_eligible,
            'event_id'])
        outcome = _run_account(policy, eligible)
        if outcome['status'] == 'completed':
            _metrics_rows(policy, outcome['arm']['cases'], metric_rows)
        results[policy] = outcome
        gc.collect()

    account_table = pd.DataFrame(metric_rows)
    account_table.to_csv(EXP / cfg['outputs']['account_metrics'], index=False)
    summary = {
        'experiment_id': cfg['experiment_id'], 'builder': 'yoyo/evaluation/cloud_close_reclaim_acceptance.py',
        'policy_counts': features.acceptance_state.value_counts(dropna=False).to_dict(),
        'positive_turnover_counts': features.groupby('acceptance_state').positive_turnover_cap_eligible.sum().to_dict(),
        'missing_and_unmatched': {
            'incomplete_last3': int((~features.h1_before_entry_complete).sum()),
            'missing_monday_range': int(features.monday_range_state.ne('ready').sum()),
            'missing_candidate_audit_reference': int((~features.candidate_anchor_crosschecked).sum()),
            'nonpositive_or_missing_turnover': int((~features.positive_turnover_cap_eligible).sum()),
        },
        'audit': audit, 'accounts': results,
        'control_definition': 'all_observable is the no-gate baseline/control; the queue source has actual V1.7 entries only, not separate matched non-signal rows.',
        'native_vectorbt': 'Conditional cohorts use cloud_close_liquid_queue.main -> actual shared-cash VectorBT portfolio; this builder contains no fill/PnL engine.',
        'limitations': ['No funding or exit-depth model; the source account uses the preregistered 10% prior-hour average-volume cap.'],
    }
    _safe_json(EXP / cfg['outputs']['summary'], summary)
    _safe_json(EXP / cfg['outputs']['paths_audit'], {
        'builder_config': str((EXP / 'config.json').relative_to(ROOT)),
        'feature_audit': str((EXP / 'feature_audit.json').relative_to(ROOT)),
        'feature_rows': str((EXP / 'confirmation_features.csv.gz').relative_to(ROOT)),
        'source_coverage': str((EXP / 'source_coverage.csv').relative_to(ROOT)),
        'account_metrics': str((EXP / 'account_metrics.csv').relative_to(ROOT)),
        'groups': {name: {'status': value['status'], 'eligible_event_ids': value['eligible_event_ids']}
                   for name, value in results.items()},
        'source_lineage': {'actual_signal_events': 'cloud_close_liquid_queue.repair.pool() at tp_r=0',
                           'H1_prices': 'same corrected quotes loader returned by repair.pool()',
                           'reference_anchor': 'frozen original signal reference (or exact inversion of frozen stop), checked against full NY Monday H1 1.618 range'},
    })
    print(account_table[account_table.period.eq('all')].to_string(index=False), flush=True)
    return summary


def _safe_json(path: Path, value: dict) -> None:
    def normalize(item):
        if isinstance(item, dict):
            return {str(k): normalize(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(v) for v in item]
        if isinstance(item, np.integer):
            return int(item)
        if isinstance(item, np.floating):
            return float(item) if np.isfinite(item) else None
        if isinstance(item, np.bool_):
            return bool(item)
        if isinstance(item, float) and not np.isfinite(item):
            return None
        if item is pd.NA or item is pd.NaT:
            return None
        return item

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(normalize(value), ensure_ascii=False, indent=2,
                                    allow_nan=False, default=str) + '\n', encoding='utf-8')
    temporary.replace(path)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--mini-check', action='store_true')
    group.add_argument('--run', action='store_true')
    args = parser.parse_args(argv)
    if args.mini_check:
        print(json.dumps(mini_check(), ensure_ascii=False, indent=2))
    else:
        run()


if __name__ == '__main__':
    main()
