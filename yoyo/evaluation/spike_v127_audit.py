"""Independent ledger checks for the frozen V12.7 held-age comparison.

Validates receipts and launch-commit source blobs, economic identities, the
held-evidence age contract that defines the single variable, candidate
causality, shared executions, V12.6 run_v2 baseline parity, and (when a
summary directory is given) an independent recomputation of every published
headline metric. No strategy is selected and no production state is touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v126_htf_report import load_run, sha
from yoyo.evaluation.spike_v10_4_study import SPLIT
from yoyo.evaluation.spike_v127_held_age import ARMS, HELD_LIFE


def _age(frame: pd.DataFrame) -> pd.Series:
    """Age in chart bars of the paired evidence at its joint bar."""
    anchor = np.where(frame.pair_source.eq('htf'), frame.visible_i, frame.break_i)
    return frame.signal_i - anchor


def _recompute(closed: pd.DataFrame, split: pd.Timestamp) -> pd.DataFrame:
    cohort = np.where(closed.signal_close >= split, 'later',
                      np.where(closed.exit_time < split, 'earlier', 'cross_split'))
    rows = []
    for arm in ARMS:
        for period in ('full', 'earlier', 'later'):
            unit = closed.loc[closed.arm.eq(arm) & (True if period == 'full' else cohort == period)]
            rows.append({'arm': arm, 'period': period, 'n': len(unit), 'win_rate': float(unit.net_r.gt(0).mean()),
                         'sum_net_r': float(unit.net_r.sum()), 'mean_net_r': float(unit.net_r.mean()),
                         'gt5_final_net_r': int(unit.net_r.gt(5).sum()), 'gt10_final_net_r': int(unit.net_r.gt(10).sum())})
    return pd.DataFrame(rows)


def audit(root: Path, output: Path, summary: Path | None = None):
    identity, tables = load_run(root)
    config = identity['config']
    if identity['subset'] or len(identity['symbols']) != config['expected_symbols']:
        raise ValueError('full frozen universe required')
    if config['split'] != SPLIT.isoformat() or config['held_life_bars'] != HELD_LIFE:
        raise ValueError('frozen split or held life mismatch')
    source_commit = json.loads((root / 'started.json').read_text())['source_commit']
    if not re.fullmatch('[0-9a-f]{40}', source_commit):
        raise ValueError('invalid source commit')
    repository = Path(__file__).resolve().parents[2]
    for path, expected in identity['source'].items():
        relative = Path(path).resolve().relative_to(repository)
        blob = subprocess.check_output(['git', 'show', f'{source_commit}:{relative}'], cwd=repository)
        if hashlib.sha256(blob).hexdigest() != expected:
            raise ValueError(f'replay source was not in its launch commit: {path}')
    d, t, s, c = (tables[k] for k in ('decisions', 'trades', 'statuses', 'controls'))
    t['censored'] = t.censored.astype(bool)
    assert not d.duplicated(['arm', 'trade_key']).any()
    assert not d.duplicated(['arm', 'stream_symbol', 'box_entry_i']).any()
    assert not t.duplicated(['arm', 'trade_key']).any()
    assert not s.duplicated(['arm', 'trade_key']).any()
    assert len(s) == len(d)
    for arm in ARMS:
        assert set(s.loc[s.arm.eq(arm), 'trade_key']) == set(d.loc[d.arm.eq(arm), 'trade_key'])
        assert d.loc[d.arm.eq(arm), 'held_life'].eq(HELD_LIFE[arm]).all()
    # Single variable: box-first pairs the current break; break-first evidence
    # must be strictly older than the joint bar and within its arm's life.
    age, box_first = _age(d), d.order.eq('box-first')
    assert age[box_first].eq(0).all()
    for arm in ARMS:
        unit = ~box_first & d.arm.eq(arm)
        assert age[unit].ge(1).all() and age[unit].le(HELD_LIFE[arm]).all()
    clock = {k: pd.to_datetime(d[k], utc=True) for k in ('signal_bar_open', 'signal_close', 'v9_reference_time',
             'break_close_time', 'cross_close_time', 'next_open_time')}
    assert (clock['signal_close'] - clock['signal_bar_open']).eq(pd.Timedelta(minutes=15)).all()
    assert (clock['v9_reference_time'] <= clock['signal_close']).all()
    assert (clock['break_close_time'] <= clock['signal_close']).all()
    assert (clock['cross_close_time'] <= clock['break_close_time']).all()
    assert d.cross_i.ge(d.born_i).all() and d.cross_i.le(d.break_i).all()
    assert (d.signal_i - d.box_entry_i).eq(d.bars_after_v9).all()
    closed = t.loc[~t.censored].copy()
    assert closed.initial_risk_frac.gt(0).all()
    np.testing.assert_allclose(closed.gross_return - closed.net_return, .002, atol=1e-12)
    np.testing.assert_allclose(closed.net_r, closed.net_return / closed.initial_risk_frac, atol=1e-10)
    np.testing.assert_allclose(closed.gross_return, (closed.exit_price - closed.entry_price) / closed.entry_price, atol=1e-12)
    np.testing.assert_allclose(t.initial_risk_frac, t.initial_risk / t.entry_price, atol=1e-12)
    joined = t.merge(d[['arm', 'trade_key', 'next_open_time', 'next_open_price']], on=['arm', 'trade_key'], validate='one_to_one')
    assert pd.to_datetime(joined.entry_time, utc=True).eq(pd.to_datetime(joined.next_open_time, utc=True)).all()
    np.testing.assert_allclose(joined.entry_price, joined.next_open_price, atol=1e-12)
    for _, g in t.groupby(['stream_symbol', 'arm']):
        ordered = g.sort_values('signal_i')
        assert (ordered.signal_i.to_numpy()[1:] >= ordered.exit_i.to_numpy()[:-1]).all()
    a = t.loc[t.arm.eq(ARMS[0])].set_index('trade_key'); b = t.loc[t.arm.eq(ARMS[1])].set_index('trade_key')
    shared = a.index.intersection(b.index)
    cols = ['entry_price', 'initial_stop', 'initial_risk', 'exit_price', 'gross_return', 'net_return', 'net_r', 'exit_i']
    np.testing.assert_allclose(a.loc[shared, cols].to_numpy(float), b.loc[shared, cols].to_numpy(float), atol=1e-12, equal_nan=True)
    assert a.loc[shared, 'exit_reason'].equals(b.loc[shared, 'exit_reason'])
    assert set(zip(c.arm, c.trade_key)) == set(zip(t.arm, t.trade_key))
    assert c.loc[c.matched, ['control_net_r', 'control_net_return']].notna().all().all()
    # The baseline arm is the frozen V12.6 run, recomputed from its own inputs.
    _, reference = load_run(Path(config['parity_reference_run']))
    ours = a.sort_index(); theirs = reference['trades'].loc[reference['trades'].arm.eq('baseline')].set_index('trade_key').sort_index()
    assert ours.index.equals(theirs.index)
    np.testing.assert_allclose(ours[cols].to_numpy(float), theirs[cols].to_numpy(float), atol=1e-12, equal_nan=True)
    published = None
    if summary is not None:
        closed_metrics = closed.assign(signal_close=pd.to_datetime(closed.signal_close, utc=True),
                                       exit_time=pd.to_datetime(closed.exit_time, utc=True))
        mine = _recompute(closed_metrics, pd.Timestamp(config['split']))
        theirs_metrics = pd.read_csv(summary / 'metrics.csv')
        merged = mine.merge(theirs_metrics, on=['arm', 'period'], suffixes=('_audit', '_report'), validate='one_to_one')
        for column in ('n', 'win_rate', 'sum_net_r', 'mean_net_r', 'gt5_final_net_r', 'gt10_final_net_r'):
            np.testing.assert_allclose(merged[f'{column}_audit'].to_numpy(float), merged[f'{column}_report'].to_numpy(float), atol=1e-9)
        published = {'rows': len(merged), 'columns': 6, 'summary_sha256': sha(summary / 'metrics.csv')}
    stats = {'status': 'pass', 'symbols': len(identity['symbols']), 'candidate_rows': len(d), 'status_rows': len(s),
             'trade_rows': len(t), 'closed_rows': len(closed), 'control_rows': len(c), 'shared_executions': len(shared),
             'candidates_per_arm': {arm: int(d.arm.eq(arm).sum()) for arm in ARMS},
             'break_first_per_arm': {arm: int((~box_first & d.arm.eq(arm)).sum()) for arm in ARMS},
             'max_break_first_age': {arm: int(age[~box_first & d.arm.eq(arm)].max()) for arm in ARMS},
             'baseline_parity_trades': len(ours), 'independent_metric_recomputation': published,
             'source_hashes_verified': len(identity['source']), 'source_commit_verified': source_commit,
             'identity_sha256': sha(root / 'identity.json'), 'manifest_sha256': sha(root / 'manifest.json'),
             'audit_source_sha256': sha(Path(__file__)),
             'checks': ['receipt_and_leaf_hashes', 'launch_commit_source_blobs', 'full_universe',
                        'unique_joint_per_box_per_arm', 'complete_status_book_per_arm', 'held_age_contract',
                        'crossing_break_and_fill_chronology', 'serial_no_overlap', '20bp_cost', 'R_denominator',
                        'long_price_return', 'same_event_same_execution', 'control_inventory',
                        'v126_run_v2_baseline_parity', 'independent_metric_identities'],
             'limitations': ['no_native_TradingView_pivot_parity', 'event_R_is_not_account_equity',
                             'historical_window_already_examined_not_a_blind_forward_test']}
    if output.exists():
        raise ValueError('refuse to overwrite an audit')
    output.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--summary', type=Path)
    args = parser.parse_args()
    audit(args.input, args.output, args.summary)
