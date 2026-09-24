"""Aggregate authenticated SPIKE V12.8 VWAP/TWAP replay streams.

This module consumes receipt-bound outputs from ``spike_v128_vwap_study`` only.
It never replays entries or redraws a matched control. Early outcomes that have
not exited before the 2025 split are counted as cross-split and excluded from
early/all closed-trade metrics. The four preregistered later raw-arm control
tests use week-clustered sign flips and bootstrap intervals.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v128_expansion_report import holm, inference


EXP = Path('experiments/active/exp-spike-v128-vwap-20260924-v1')
CONFIG = EXP / 'config.json'
FEATURES = ('vwap', 'twap')
POLICIES = ('baseline', 'vwap_near', 'twap_near')
ARMS = ('v9_both', 'joint')
TABLE_COLUMNS = {
    'candidate_outcomes': (
        'trade_key', 'arm', 'symbol', 'timeframe_min', 'signal_i', 'signal_close', 'side',
        'status', 'entry_time', 'exit_time', 'net_return', 'gross_return', 'net_r', 'gross_r',
        'censored', 'vwap_distance', 'twap_distance', 'vwap_near', 'twap_near',
    ),
    'controls': (
        'trade_key', 'arm', 'symbol', 'timeframe_min', 'side', 'matched', 'control_pool',
        'control_exit_time', 'control_net_return', 'control_net_r', 'control_censored',
        'control_vwap_distance', 'control_twap_distance', 'control_vwap_near', 'control_twap_near',
    ),
    'serial_trades': (
        'trade_key', 'arm', 'symbol', 'timeframe_min', 'side', 'policy', 'status', 'signal_close',
        'entry_time', 'exit_time', 'net_return', 'gross_return', 'net_r', 'gross_r', 'censored',
        'vwap_distance', 'twap_distance', 'vwap_near', 'twap_near', 'evaluation_fold',
    ),
    'serial_statuses': (
        'trade_key', 'arm', 'symbol', 'timeframe_min', 'signal_close', 'policy', 'status',
    ),
}


def digest(path: str | Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            hasher.update(block)
    return hasher.hexdigest()


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'cannot read {path}') from exc


def _read_required_csv(path: Path, columns: tuple[str, ...]) -> pd.DataFrame:
    try:
        header = set(pd.read_csv(path, nrows=0).columns)
        missing = set(columns) - header
        if missing:
            raise ValueError(f'{path} lacks required columns: {sorted(missing)}')
        return pd.read_csv(path, usecols=list(columns), low_memory=False)
    except (OSError, pd.errors.EmptyDataError) as exc:
        raise ValueError(f'cannot read ledger {path}') from exc


def _expected_streams(symbols: list[str], timeframes: list[int]) -> set[str]:
    return {f'{symbol}_{int(minutes)}m' for symbol in symbols for minutes in timeframes}


def authenticate_run(run_path: str | Path) -> dict:
    """Check the study identity, frozen config, thresholds, receipts, and ledgers."""
    run = Path(run_path)
    identity = _read_json(run / 'identity.json')
    manifest = _read_json(run / 'manifest.json')
    cfg = _read_json(CONFIG)
    if not manifest.get('complete') or manifest.get('errors'):
        raise ValueError('study run is incomplete or has stream errors')
    if manifest.get('run_identity') != fingerprint(identity):
        raise ValueError('study identity fingerprint mismatch')
    if identity.get('config') != cfg or identity.get('config_sha256') != digest(CONFIG):
        raise ValueError('study config differs from the frozen experiment config')
    if float(cfg.get('round_trip_cost', math.nan)) != 0.002:
        raise ValueError('the fixed 20bp cost contract changed')
    symbols = list(identity.get('symbols', []))
    timeframes = [int(x) for x in identity.get('timeframes', [])]
    if (not symbols or len(set(symbols)) != len(symbols) or
            timeframes != [int(x) for x in cfg.get('timeframes', [])] or
            identity.get('inputs') is None or set(identity['inputs']) != set(symbols)):
        raise ValueError('study identity has invalid symbol/timeframe/input coverage')
    if identity.get('subset') not in (True, False) or manifest.get('full_universe') != (not identity['subset']):
        raise ValueError('subset and full-universe declarations disagree')
    code_hashes = identity.get('code')
    if not isinstance(code_hashes, dict) or not code_hashes:
        raise ValueError('study identity lacks source-code hashes')
    expected = _expected_streams(symbols, timeframes)
    receipt_map = manifest.get('receipts', {})
    if set(receipt_map) != expected:
        raise ValueError('study manifest stream inventory mismatch')
    for source, expected_sha in identity.get('code', {}).items():
        path = Path(source)
        if not path.is_file() or digest(path) != expected_sha:
            raise ValueError(f'study source identity changed: {source}')

    threshold_path = run / 'thresholds.json'
    threshold_sha = digest(threshold_path) if threshold_path.is_file() else None
    if not threshold_sha or manifest.get('thresholds_sha256') != threshold_sha:
        raise ValueError('threshold file hash mismatch')
    thresholds = _read_json(threshold_path)
    for minutes in timeframes:
        for feature in FEATURES:
            item = thresholds.get(str(minutes), {}).get(feature, {})
            q50, q10, n = item.get('q50'), item.get('q10'), item.get('n')
            if (not isinstance(q50, (int, float)) or not isinstance(q10, (int, float)) or
                    not math.isfinite(q50) or not math.isfinite(q10) or q10 < 0 or q50 < q10 or int(n or 0) < 1):
                raise ValueError(f'invalid frozen threshold {minutes}/{feature}')

    receipts = {}
    for key in sorted(expected):
        folder = run / 'streams' / key
        receipt_path = folder / 'receipt.json'
        if digest(receipt_path) != receipt_map[key]:
            raise ValueError(f'stream receipt hash mismatch: {key}')
        receipt = _read_json(receipt_path)
        symbol, minutes = key.rsplit('_', 1)[0], int(key.rsplit('_', 1)[1][:-1])
        if (receipt.get('run_identity') != manifest['run_identity'] or receipt.get('status') != 'complete' or
                receipt.get('baseline_parity') is not True or receipt.get('symbol') != symbol or
                int(receipt.get('minutes', -1)) != minutes or
                receipt.get('input_sha256') != identity['inputs'].get(symbol) or
                receipt.get('thresholds_sha256') != threshold_sha):
            raise ValueError(f'invalid stream identity/parity: {key}')
        feature_receipt = run / 'features' / key / 'receipt.json'
        if digest(feature_receipt) != receipt.get('feature_receipt_sha256'):
            raise ValueError(f'feature receipt hash mismatch: {key}')
        feature_data = _read_json(feature_receipt)
        if (feature_data.get('run_identity') != manifest['run_identity'] or
                feature_data.get('status') != 'complete' or feature_data.get('input_sha256') != identity['inputs'][symbol]):
            raise ValueError(f'invalid feature receipt: {key}')
        for name, columns in TABLE_COLUMNS.items():
            filename = name + '.csv.gz'
            expected_sha = receipt.get('files', {}).get(filename)
            path = folder / filename
            if not expected_sha or digest(path) != expected_sha:
                raise ValueError(f'ledger hash mismatch: {key}/{filename}')
            # Check headers without parsing every ledger twice.
            header = set(pd.read_csv(path, nrows=0).columns)
            if set(columns) - header:
                raise ValueError(f'{path} lacks required columns: {sorted(set(columns) - header)}')
        receipts[key] = receipt
    return {'run': run, 'identity': identity, 'manifest': manifest, 'config': cfg,
            'thresholds': thresholds, 'thresholds_sha256': threshold_sha, 'receipts': receipts}


def _bool(series: pd.Series, label: str, *, missing: bool = False) -> pd.Series:
    text = series.astype('string').str.strip().str.lower()
    true = text.isin(('true', '1', '1.0'))
    false = text.isin(('false', '0', '0.0'))
    unknown = ~(true | false | text.isna())
    if unknown.any():
        raise ValueError(f'invalid boolean in {label}')
    out = true.astype(bool)
    if missing:
        out = out.where(text.notna(), missing)
    return out


def _normalize(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for column in ('signal_close', 'entry_time', 'exit_time', 'control_exit_time'):
        if column in frame:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors='coerce')
    for column in ('censored', 'matched', 'control_censored', 'vwap_near', 'twap_near',
                   'control_vwap_near', 'control_twap_near'):
        if column in frame:
            frame[column] = _bool(frame[column], f'{name}.{column}', missing=(column == 'control_censored'))
    for column in ('timeframe_min', 'signal_i'):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors='coerce')
    for column in ('net_return', 'gross_return', 'net_r', 'gross_r', 'vwap_distance', 'twap_distance',
                   'control_net_return', 'control_net_r', 'control_vwap_distance', 'control_twap_distance'):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors='coerce')
    if 'signal_close' in frame and frame.signal_close.isna().any():
        raise ValueError(f'{name} has missing signal clocks')
    return frame


def _verify_gate(frame: pd.DataFrame, thresholds: dict, minutes: int, *, control: bool = False) -> None:
    prefix = 'control_' if control else ''
    for feature in FEATURES:
        distance = pd.to_numeric(frame[prefix + feature + '_distance'], errors='coerce')
        actual = frame[prefix + feature + '_near'].astype(bool)
        cutoff = float(thresholds[str(minutes)][feature]['q50'])
        expected = np.isfinite(distance.to_numpy(float)) & (np.abs(distance.to_numpy(float)) <= cutoff)
        if not np.array_equal(actual.to_numpy(bool), expected):
            raise ValueError(f'{prefix}{feature} gate does not match the frozen threshold')


def _check_stream_tables(key: str, tables: dict[str, pd.DataFrame], receipt: dict,
                          thresholds: dict) -> None:
    symbol, minutes_text = key.rsplit('_', 1)
    minutes = int(minutes_text[:-1])
    candidates, controls = tables['candidate_outcomes'], tables['controls']
    trades, statuses = tables['serial_trades'], tables['serial_statuses']
    for name, frame in tables.items():
        if len(frame) and (set(frame.symbol.astype(str)) != {symbol} or
                           set(frame.timeframe_min.astype(int)) != {minutes}):
            raise ValueError(f'{key}/{name} contains another stream identity')
    if candidates.trade_key.duplicated().any():
        raise ValueError(f'{key} has duplicate raw candidates')
    if controls.trade_key.duplicated().any() or set(controls.trade_key) != set(candidates.trade_key):
        raise ValueError(f'{key} does not preserve exactly one inherited control per candidate')
    if set(controls.control_pool.dropna().astype(str)) - {'baseline'}:
        raise ValueError(f'{key} has an unexpected control pool')
    if trades.duplicated(['policy', 'trade_key']).any() or statuses.duplicated(['policy', 'trade_key']).any():
        raise ValueError(f'{key} has duplicate serial keys')
    if set(trades.policy.dropna()) - set(POLICIES) or set(statuses.policy.dropna()) - set(POLICIES):
        raise ValueError(f'{key} has an unexpected entry policy')
    if set(trades.trade_key) - set(candidates.trade_key) or set(statuses.trade_key) - set(candidates.trade_key):
        raise ValueError(f'{key} serial ledger contains a non-candidate key')
    if not set(trades[['policy', 'trade_key']].itertuples(index=False, name=None)) <= set(
            statuses[['policy', 'trade_key']].itertuples(index=False, name=None)):
        raise ValueError(f'{key} selected trade lacks a status row')
    if set(candidates.arm.dropna()) - set(ARMS):
        raise ValueError(f'{key} has an unexpected arm')
    _verify_gate(candidates, thresholds, minutes)
    _verify_gate(controls, thresholds, minutes, control=True)

    # Selection changes occupancy only. Every selected trade must retain its candidate's frozen outcome.
    if len(trades):
        raw = candidates.set_index('trade_key')
        selected = trades.set_index('trade_key')
        for column in ('arm', 'side', 'signal_close', 'entry_time', 'exit_time', 'censored'):
            lhs, rhs = selected[column], raw.loc[selected.index, column]
            if column in ('signal_close', 'entry_time', 'exit_time'):
                same = lhs.eq(rhs) | (lhs.isna() & rhs.isna())
                if not same.all():
                    raise ValueError(f'{key} serial {column} differs from frozen candidate outcome')
            elif column == 'censored':
                if not np.array_equal(lhs.to_numpy(bool), rhs.to_numpy(bool)):
                    raise ValueError(f'{key} serial censor status differs from frozen candidate outcome')
            elif not lhs.astype(str).equals(rhs.astype(str)):
                raise ValueError(f'{key} serial {column} differs from frozen candidate outcome')
        for column in ('gross_return', 'net_return', 'gross_r', 'net_r'):
            lhs = selected[column].to_numpy(float)
            rhs = raw.loc[selected.index, column].to_numpy(float)
            if not np.allclose(lhs, rhs, rtol=0, atol=1e-12, equal_nan=True):
                raise ValueError(f'{key} serial {column} differs from frozen candidate outcome')
        closed = trades.loc[~trades.censored]
        if len(closed):
            values = closed[['gross_return', 'net_return']].to_numpy(float)
            if not np.isfinite(values).all() or not np.allclose(values[:, 0] - values[:, 1], .002, atol=1e-10, rtol=0):
                raise ValueError(f'{key} closed outcome violates fixed 20bp cost')
        status_lookup = statuses.set_index(['policy', 'trade_key']).status
        got = trades.apply(lambda row: status_lookup.loc[(row.policy, row.trade_key)], axis=1)
        if not np.array_equal(got.astype(str).to_numpy(), trades.status.astype(str).to_numpy()):
            raise ValueError(f'{key} selected status differs from serial status ledger')


def _week(signal: pd.Series) -> pd.Series:
    monday = signal.dt.normalize() - pd.to_timedelta(signal.dt.dayofweek, unit='D')
    return monday.dt.strftime('%Y-%m-%d')


def _entry_cohort(frame: pd.DataFrame, period: str, start: pd.Timestamp,
                  split: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    in_window = frame[(frame.signal_close >= start) & (frame.signal_close < end)]
    if period == 'all':
        return in_window
    if period == 'earlier':
        return in_window[in_window.signal_close < split]
    if period == 'later':
        return in_window[in_window.signal_close >= split]
    raise ValueError(f'unknown period: {period}')


def _mature_closed(frame: pd.DataFrame, period: str, split: pd.Timestamp) -> pd.DataFrame:
    closed = frame[frame.status.eq('closed') & ~frame.censored & frame.exit_time.notna()]
    if period == 'earlier':
        return closed[closed.exit_time < split]
    if period == 'later':
        return closed[closed.signal_close >= split]
    # The early fitted fold is descriptive only when its exit is already known at the split.
    return closed[(closed.signal_close >= split) | (closed.exit_time < split)]


def _crosscut(frame: pd.DataFrame, split: pd.Timestamp) -> pd.DataFrame:
    return frame[(frame.signal_close < split) & (frame.exit_time >= split) & ~frame.censored]


def _stats(closed: pd.DataFrame) -> dict:
    n = len(closed)
    if n == 0:
        return {'wins': 0, 'win_rate': math.nan, 'mean_gross_bp': math.nan, 'mean_net_bp': math.nan,
                'mean_net_r': math.nan, 'pf_net_r': math.nan, 'net_ge3r': 0, 'net_ge3r_rate': math.nan,
                'net_ge5r': 0, 'net_ge5r_rate': math.nan, 'net_ge10r': 0, 'net_ge10r_rate': math.nan}
    net_r = closed.net_r.to_numpy(float)
    wins = closed.net_return.to_numpy(float) > 0
    positive, negative = net_r[net_r > 0], net_r[net_r < 0]
    result = {'wins': int(wins.sum()), 'win_rate': float(wins.mean()),
              'mean_gross_bp': float(closed.gross_return.mean() * 1e4),
              'mean_net_bp': float(closed.net_return.mean() * 1e4),
              'mean_net_r': float(np.mean(net_r)),
              'pf_net_r': float(positive.sum() / -negative.sum()) if len(negative) else math.nan}
    for level in (3, 5, 10):
        count = int((net_r >= level).sum())
        result[f'net_ge{level}r'] = count
        result[f'net_ge{level}r_rate'] = count / n
    return result


def _retention(base: pd.DataFrame, treated: pd.DataFrame) -> dict:
    base_keys, treated_keys = set(base.trade_key), set(treated.trade_key)
    retained = len(base_keys & treated_keys)
    result = {'baseline_closed': len(base_keys), 'samekey_retained': retained,
              'samekey_lost': len(base_keys - treated_keys), 'samekey_new': len(treated_keys - base_keys),
              'retention_rate': retained / len(base_keys) if base_keys else math.nan}
    for level in (3, 5, 10):
        old = base[base.net_r >= level]
        new = treated[treated.net_r >= level]
        old_keys, new_keys = set(old.trade_key), set(new.trade_key)
        keep = len(old_keys & new_keys)
        result.update({f'baseline_ge{level}r': len(old_keys), f'samekey_retained_ge{level}r': keep,
                       f'samekey_lost_ge{level}r': len(old_keys - new_keys),
                       f'samekey_new_ge{level}r': len(new_keys - old_keys),
                       f'retention_rate_ge{level}r': keep / len(old_keys) if old_keys else math.nan})
    return result


def _seed(seed: int, *parts) -> int:
    token = '|'.join(map(str, parts))
    return int(seed + sum((i + 1) * ord(ch) for i, ch in enumerate(token))) % (2**32 - 1)


def _policy_bootstrap(base: pd.DataFrame, treated: pd.DataFrame, seed: int, bootstrap: int) -> dict:
    """Paired calendar-week bootstrap of policy mean-bp and win-rate differences."""
    if base.empty or treated.empty:
        return {'bootstrap_weeks': 0, 'valid_bootstrap_draws': 0,
                'delta_mean_net_bp': math.nan, 'ci_low_mean_net_bp': math.nan,
                'ci_high_mean_net_bp': math.nan, 'delta_win_rate': math.nan,
                'ci_low_win_rate': math.nan, 'ci_high_win_rate': math.nan}
    a, b = base.copy(), treated.copy()
    a['_week'] = _week(a.signal_close); b['_week'] = _week(b.signal_close)
    weeks = sorted(set(a._week) | set(b._week))
    if len(weeks) < 2:
        return {'bootstrap_weeks': len(weeks), 'valid_bootstrap_draws': 0,
                'delta_mean_net_bp': float(b.net_return.mean() - a.net_return.mean()) * 1e4,
                'ci_low_mean_net_bp': math.nan, 'ci_high_mean_net_bp': math.nan,
                'delta_win_rate': float((b.net_return > 0).mean() - (a.net_return > 0).mean()),
                'ci_low_win_rate': math.nan, 'ci_high_win_rate': math.nan}
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(weeks), size=(bootstrap, len(weeks)))
    counts, bp_sums, win_sums = [], [], []
    for frame in (a, b):
        grouped = frame.assign(_bp=frame.net_return * 1e4, _win=(frame.net_return > 0).astype(float)).groupby('_week')
        counts.append(grouped.size().reindex(weeks, fill_value=0).to_numpy(float))
        bp_sums.append(grouped['_bp'].sum().reindex(weeks, fill_value=0).to_numpy(float))
        win_sums.append(grouped['_win'].sum().reindex(weeks, fill_value=0).to_numpy(float))
    den = [x[draws].sum(axis=1) for x in counts]
    valid = (den[0] > 0) & (den[1] > 0)
    deltas = {}
    for label, sums in (('mean_net_bp', bp_sums), ('win_rate', win_sums)):
        sampled = sums[1][draws].sum(axis=1)[valid] / den[1][valid] - sums[0][draws].sum(axis=1)[valid] / den[0][valid]
        deltas[label] = (float(np.quantile(sampled, .025)) if len(sampled) else math.nan,
                         float(np.quantile(sampled, .975)) if len(sampled) else math.nan)
    return {'bootstrap_weeks': len(weeks), 'valid_bootstrap_draws': int(valid.sum()),
            'delta_mean_net_bp': float((b.net_return.mean() - a.net_return.mean()) * 1e4),
            'ci_low_mean_net_bp': deltas['mean_net_bp'][0], 'ci_high_mean_net_bp': deltas['mean_net_bp'][1],
            'delta_win_rate': float((b.net_return > 0).mean() - (a.net_return > 0).mean()),
            'ci_low_win_rate': deltas['win_rate'][0], 'ci_high_win_rate': deltas['win_rate'][1]}


def _control_row(target: pd.DataFrame, controls: pd.DataFrame, *, period: str, split: pd.Timestamp,
                 policy: str, feature: str | None, mode: str, cfg: dict) -> dict:
    joined = target.merge(controls, on='trade_key', how='left', validate='one_to_one', suffixes=('', '_control'))
    assigned = joined.control_pool.notna() if 'control_pool' in joined else joined.matched.notna()
    matched = assigned & joined.matched.fillna(False)
    gate_pass = pd.Series(True, index=joined.index)
    if mode == 'same_gate':
        if feature is None:
            raise ValueError('same-gate comparison lacks its feature')
        gate_pass = joined['control_' + feature + '_near'].fillna(False).astype(bool)
    if mode not in ('unconditional', 'same_gate'):
        raise ValueError(f'unknown control mode: {mode}')
    finite = np.isfinite(pd.to_numeric(joined.control_net_return, errors='coerce'))
    control_exit = pd.to_datetime(joined.control_exit_time, utc=True, errors='coerce')
    mature_control = control_exit.notna()
    early_target = joined.signal_close < split
    if period in ('earlier', 'all'):
        mature_control &= ~early_target | control_exit.lt(split)
    control_valid = ~joined.control_censored.fillna(True) & finite & mature_control
    usable = matched & gate_pass & control_valid
    pair = joined.loc[usable].copy()
    if len(pair):
        excess = (pair.net_return.to_numpy(float) - pair.control_net_return.to_numpy(float)) * 1e4
        weeks = _week(pair.signal_close)
        infer = inference(excess, weeks, int(cfg['statistics_seed']), int(cfg['bootstrap']))
        target_bp = pair.net_return.to_numpy(float) * 1e4
        random_bp = pair.control_net_return.to_numpy(float) * 1e4
        target_win = pair.net_return.to_numpy(float) > 0
        random_win = pair.control_net_return.to_numpy(float) > 0
        infer.update({'target_mean_net_bp': float(target_bp.mean()), 'random_mean_net_bp': float(random_bp.mean()),
                      'target_win_rate': float(target_win.mean()), 'random_win_rate': float(random_win.mean()),
                      'win_rate_excess': float(target_win.mean() - random_win.mean())})
    else:
        infer = {'matched': 0, 'weeks': 0, 'mean_excess_bp': math.nan, 'p_one_sided': math.nan,
                 'ci_low_bp': math.nan, 'ci_high_bp': math.nan, 'target_mean_net_bp': math.nan,
                 'random_mean_net_bp': math.nan, 'target_win_rate': math.nan,
                 'random_win_rate': math.nan, 'win_rate_excess': math.nan}
    return {'policy': policy, 'feature': feature or 'none', 'control_mode': mode,
            'target_closed': int(len(target)), 'draw_assigned': int(assigned.sum()),
            'matched_draws': int(matched.sum()),
            'control_gate_pass_draws': int((matched & gate_pass).sum()),
            'control_gate_rejected': int((matched & ~gate_pass).sum()),
            'control_censored_or_missing': int((matched & ~control_valid).sum()),
            'paired_closed': int(len(pair)), 'paired_coverage': len(pair) / len(target) if len(target) else math.nan,
            **infer}


def _auc(score: pd.Series, outcome: pd.Series) -> float:
    values = pd.DataFrame({'score': pd.to_numeric(score, errors='coerce'), 'outcome': outcome}).dropna()
    values = values[np.isfinite(values.score)]
    n_pos = int(values.outcome.astype(bool).sum())
    n_neg = len(values) - n_pos
    if not n_pos or not n_neg:
        return math.nan
    ranks = values.score.rank(method='average').to_numpy(float)
    return float((ranks[values.outcome.astype(bool).to_numpy()].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _fixed_q10_diagnostic(baseline_serial: pd.DataFrame, controls: pd.DataFrame, *, minutes: int,
                          feature: str, cutoff: float, split: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Describe the frozen earlier-q10 slice of later baseline serial trades.

    This descriptive filter is applied to the unchanged baseline serial cohort,
    not re-arbitrated as a new strategy. It uses the one inherited control draw
    and reports unconditional and same-q10 support without replacing controls.
    """
    base = baseline_serial[(baseline_serial.timeframe_min == minutes) & baseline_serial.arm.eq('v9_both') &
                           baseline_serial.policy.eq('baseline') & (baseline_serial.signal_close >= split) &
                           (baseline_serial.signal_close < end)].copy()
    distance = pd.to_numeric(base[feature + '_distance'], errors='coerce').abs()
    selected = base[np.isfinite(distance) & distance.le(cutoff)].copy()
    row = {'diagnostic': 'frozen_early_q10_baseline_serial_later', 'period': 'later',
           'timeframe_min': minutes, 'arm': 'v9_both', 'feature': feature,
           'q10_cutoff': cutoff, 'baseline_serial_closed': int(len(base)),
           'q10_closed': int(len(selected)), **_stats(selected)}
    joined = selected.merge(controls, on='trade_key', how='left', validate='one_to_one', suffixes=('', '_control'))
    matched = joined.matched.fillna(False).astype(bool)
    control_exit = pd.to_datetime(joined.control_exit_time, utc=True, errors='coerce')
    usable = (matched & ~joined.control_censored.fillna(True) & control_exit.notna() &
              np.isfinite(pd.to_numeric(joined.control_net_return, errors='coerce')))
    gate = pd.to_numeric(joined['control_' + feature + '_distance'], errors='coerce').abs().le(cutoff)
    conditional = usable & gate
    for prefix, mask in (('unconditional', usable), ('same_gate', conditional)):
        part = joined.loc[mask]
        target_mean = float(part.net_return.mean() * 1e4) if len(part) else math.nan
        random_mean = float(part.control_net_return.mean() * 1e4) if len(part) else math.nan
        row.update({f'{prefix}_pairs': int(len(part)), f'{prefix}_target_mean_net_bp': target_mean,
                    f'{prefix}_random_mean_net_bp': random_mean,
                    f'{prefix}_mean_excess_bp': target_mean - random_mean if len(part) else math.nan})
    row['matched_draws'] = int(matched.sum())
    row['same_gate_draws'] = int((matched & gate).sum())
    row['censored_or_missing_controls'] = int((matched & (~usable)).sum())
    return row


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(x) for x in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def aggregate(run_path: str | Path, output: str | Path) -> dict:
    """Authenticate and compactly aggregate a completed study run."""
    out = Path(output)
    if out.exists():
        raise ValueError('output exists; preserve earlier aggregation')
    auth = authenticate_run(run_path)
    run, cfg, thresholds = auth['run'], auth['config'], auth['thresholds']
    start, split, end = (pd.Timestamp(cfg[name]) for name in ('analysis_start', 'split', 'end'))
    if not start < split < end:
        raise ValueError('invalid study period boundaries')
    tables: dict[str, list[pd.DataFrame]] = {name: [] for name in TABLE_COLUMNS}
    for key in sorted(auth['receipts']):
        folder = run / 'streams' / key
        stream = {name: _normalize(name, _read_required_csv(folder / (name + '.csv.gz'), columns))
                  for name, columns in TABLE_COLUMNS.items()}
        _check_stream_tables(key, stream, auth['receipts'][key], thresholds)
        for name, frame in stream.items():
            tables[name].append(frame)
    frames = {name: (pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=TABLE_COLUMNS[name]))
              for name, parts in tables.items()}
    candidates, controls = frames['candidate_outcomes'], frames['controls']
    trades, statuses = frames['serial_trades'], frames['serial_statuses']

    metric_rows, control_rows, comparison_rows, status_rows, diagnostic_rows, direction_rows = [], [], [], [], [], []
    group_keys = set()
    for table in (statuses, trades):
        if len(table):
            group_keys.update((int(m), str(a)) for m, a in table[['timeframe_min', 'arm']].drop_duplicates().itertuples(index=False, name=None))
    for minutes, arm in sorted(group_keys):
        local_trades = trades[(trades.timeframe_min == minutes) & trades.arm.eq(arm)]
        local_statuses = statuses[(statuses.timeframe_min == minutes) & statuses.arm.eq(arm)]
        local_controls = controls[(controls.timeframe_min == minutes) & controls.arm.eq(arm)]
        for period in ('all', 'earlier', 'later'):
            base_closed = _mature_closed(_entry_cohort(local_trades[local_trades.policy.eq('baseline')], period, start, split, end), period, split)
            for policy in POLICIES:
                cohort = _entry_cohort(local_trades[local_trades.policy.eq(policy)], period, start, split, end)
                closed = _mature_closed(cohort, period, split)
                status_cohort = _entry_cohort(local_statuses[local_statuses.policy.eq(policy)], period, start, split, end)
                crossing = _crosscut(cohort, split) if period in ('all', 'earlier') else cohort.iloc[0:0]
                elapsed_days = (end - start).total_seconds() / 86400
                if period == 'earlier':
                    elapsed_days = (split - start).total_seconds() / 86400
                elif period == 'later':
                    elapsed_days = (end - split).total_seconds() / 86400
                ret = _retention(base_closed, closed)
                metric_rows.append({'period': period, 'timeframe_min': minutes, 'arm': arm, 'policy': policy,
                                    'candidates': int(len(status_cohort)), 'filled': int(len(cohort)),
                                    'closed': int(len(closed)), 'censored': int(cohort.censored.sum()),
                                    'crosscut': int(len(crossing)),
                                    'trades_per_day': len(cohort) / elapsed_days if elapsed_days > 0 else math.nan,
                                    **_stats(closed), **ret})
                if len(status_cohort):
                    part = status_cohort.groupby('status', dropna=False).size()
                    for status, count in part.items():
                        status_rows.append({'period': period, 'timeframe_min': minutes, 'arm': arm,
                                            'policy': policy, 'status': status, 'n': int(count)})

                feature = policy.replace('_near', '') if policy != 'baseline' else None
                modes = ('unconditional', 'same_gate') if feature else ('unconditional',)
                for mode in modes:
                    control_rows.append({'period': period, 'timeframe_min': minutes, 'arm': arm,
                                         **_control_row(closed, local_controls, period=period, split=split,
                                                        policy=policy, feature=feature, mode=mode, cfg=cfg)})

                paired = _policy_bootstrap(base_closed, closed,
                                            _seed(int(cfg['statistics_seed']), period, minutes, arm, policy),
                                            int(cfg['bootstrap']))
                comparison_rows.append({'period': period, 'timeframe_min': minutes, 'arm': arm,
                                        'policy': policy, **paired})

        # Direction/year strata are descriptive; cross-split early exits stay out.
        for period in ('all', 'earlier', 'later'):
            closed = _mature_closed(_entry_cohort(local_trades, period, start, split, end), period, split)
            closed = closed[closed.policy.isin(POLICIES)]
            if closed.empty:
                continue
            for (policy, side, year), part in closed.assign(year=closed.signal_close.dt.year).groupby(['policy', 'side', 'year'], dropna=False):
                diagnostic = _stats(part)
                direction_rows.append({'period': period, 'timeframe_min': minutes, 'arm': arm,
                                       'policy': policy, 'side': side, 'year': int(year),
                                       'closed': int(len(part)), **diagnostic})

    metrics = pd.DataFrame(metric_rows)
    control_table = pd.DataFrame(control_rows)
    comparisons = pd.DataFrame(comparison_rows)
    # The preregistered family is exactly later raw-arm x timeframe x feature, with same-gate controls.
    primary_keys = [(minutes, feature) for minutes in (15, 60) for feature in FEATURES]
    primary_indices = []
    for minutes, feature in primary_keys:
        mask = (control_table.period.eq('later') & control_table.timeframe_min.eq(minutes) &
                control_table.arm.eq('v9_both') & control_table.policy.eq(feature + '_near') &
                control_table.feature.eq(feature) & control_table.control_mode.eq('same_gate'))
        found = control_table.index[mask].tolist()
        if len(found) != 1:
            raise ValueError(f'primary comparison slot missing or duplicated: {minutes}/{feature}')
        primary_indices.extend(found)
    adjusted = holm(control_table.loc[primary_indices, 'p_one_sided'].to_list())
    control_table['holm_primary_p'] = np.nan
    control_table.loc[primary_indices, 'holm_primary_p'] = adjusted

    # AUC is descriptive only: frozen baseline serial later trades, score = closer to average.
    for minutes in (15, 60):
        base = trades[(trades.timeframe_min == minutes) & trades.arm.eq('v9_both') &
                      trades.policy.eq('baseline')]
        later_closed = _mature_closed(_entry_cohort(base, 'later', start, split, end), 'later', split)
        for feature in FEATURES:
            score = -later_closed[feature + '_distance'].abs()
            diagnostic_rows.append({'diagnostic': 'baseline_serial_later_auc', 'timeframe_min': minutes,
                                    'arm': 'v9_both', 'feature': feature,
                                    'n': int(np.isfinite(score.to_numpy(float)).sum()),
                                    'closed': int(len(later_closed)),
                                    'auc_net_win': _auc(score, later_closed.net_return.gt(0))})
            diagnostic_rows.append(_fixed_q10_diagnostic(
                later_closed, controls, minutes=minutes, feature=feature,
                cutoff=float(thresholds[str(minutes)][feature]['q10']), split=split, end=end))

    primary = []
    for ix, (minutes, feature) in zip(primary_indices, primary_keys):
        row = control_table.loc[ix]
        holm_p = row.holm_primary_p
        primary.append({'timeframe_min': minutes, 'feature': feature, 'paired_closed': int(row.paired_closed),
                        'weeks': int(row.weeks), 'target_mean_net_bp': row.target_mean_net_bp,
                        'random_mean_net_bp': row.random_mean_net_bp, 'mean_excess_bp': row.mean_excess_bp,
                        'p_one_sided': row.p_one_sided, 'holm_p': holm_p,
                        'positive_after_cost_and_holm': bool(pd.notna(row.target_mean_net_bp) and
                                                             row.target_mean_net_bp > 0 and
                                                             pd.notna(row.mean_excess_bp) and row.mean_excess_bp > 0 and
                                                             pd.notna(holm_p) and holm_p <= float(cfg['primary_p_threshold']))})
    summary = {
        'run_identity': auth['manifest']['run_identity'],
        'identity_sha256': digest(run / 'identity.json'),
        'manifest_sha256': digest(run / 'manifest.json'),
        'thresholds_sha256': auth['thresholds_sha256'],
        'complete': True, 'full_universe': bool(auth['manifest']['full_universe']),
        'streams': len(auth['receipts']), 'symbols': len(auth['identity']['symbols']),
        'timeframes': auth['identity']['timeframes'],
        'analysis_start': cfg['analysis_start'], 'split': cfg['split'], 'end': cfg['end'],
        'round_trip_cost': cfg['round_trip_cost'], 'thresholds': thresholds,
        'closed_metrics_exclude': 'censored outcomes and early signals exiting at/after split',
        'all_period_definition': 'mature earlier closed trades plus later closed trades; early cross-split exits excluded',
        'session_feature_limit': 'daily UTC-anchor distance varies with elapsed session time; this estimates the preregistered feature, not an anchor-independent VWAP effect',
        'primary_alpha': cfg['primary_p_threshold'], 'primary_tests': primary,
        'output_files': ['metrics.csv', 'controls.csv', 'policy_comparison.csv',
                         'status_counts.csv', 'diagnostics.csv', 'direction_year.csv'],
    }
    out.mkdir(parents=True, exist_ok=False)
    tables_out = {'metrics.csv': metrics, 'controls.csv': control_table,
                  'policy_comparison.csv': comparisons, 'status_counts.csv': pd.DataFrame(status_rows),
                  'diagnostics.csv': pd.DataFrame(diagnostic_rows), 'direction_year.csv': pd.DataFrame(direction_rows)}
    for name, frame in tables_out.items():
        frame.to_csv(out / name, index=False)
    summary['output_sha256'] = {name: digest(out / name) for name in tables_out}
    (out / 'summary.json').write_text(json.dumps(_json_safe(summary), indent=2, allow_nan=False) + '\n')
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = aggregate(args.run, args.output)
    print(json.dumps(_json_safe({'output': str(args.output), 'streams': result['streams'],
                                 'primary_tests': result['primary_tests']}), allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
