"""Independent ledger checks for the frozen V12.6 H1 research comparison.

This validates saved receipts, economic identities, candidate causality and
shared executions without generating or selecting a trading strategy. The
random controls are an offline null, not a deployable portfolio. No price
archives or production state are modified.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v126_htf_report import load_run, sha


def audit(root: Path, output: Path):
    identity, tables = load_run(root)
    if identity['subset'] or len(identity['symbols']) != identity['config']['expected_symbols']:
        raise ValueError('full frozen universe required')
    for path, expected in identity['source'].items():
        if sha(Path(path)) != expected:
            raise ValueError(f'replay source has changed: {path}')
    d, t, s, c = (tables[k] for k in ('decisions','trades','statuses','controls'))
    assert not d.trade_key.duplicated().any()
    assert not d.duplicated(['stream_symbol','box_entry_i']).any()
    assert not t.duplicated(['arm','trade_key']).any()
    assert not s.duplicated(['arm','trade_key']).any()
    assert set(d.trade_key) == set(s.trade_key)
    assert len(s) == 2*len(d)
    for arm in ('baseline','joint_h1_recheck'):
        assert set(s.loc[s.arm.eq(arm),'trade_key']) == set(d.trade_key)
    h1 = pd.to_numeric(d.h1_sma60, errors='coerce')
    expected_known = np.isfinite(h1) & (h1 > 0) & np.isfinite(d.joint_close) & d.joint_close.gt(0)
    np.testing.assert_array_equal(d.h1_known,expected_known)
    np.testing.assert_array_equal(d.h1_pass,expected_known & d.joint_close.gt(h1))
    clock = {k:pd.to_datetime(d[k],utc=True) for k in ('signal_bar_open','signal_close','v9_reference_time',
        'h1_source_close','break_close_time','next_open_time')}
    assert (clock['signal_close']-clock['signal_bar_open']).eq(pd.Timedelta(minutes=15)).all()
    assert (clock['v9_reference_time'] <= clock['signal_close']).all()
    assert (clock['break_close_time'] <= clock['signal_close']).all()
    has_h1 = d.h1_known.to_numpy(bool)
    assert clock['h1_source_close'][has_h1].eq(clock['signal_bar_open'][has_h1].dt.floor('h')).all()
    assert (d.signal_i-d.box_entry_i).eq(d.bars_after_v9).all()
    assert (clock['signal_close']-clock['v9_reference_time'] >= pd.to_timedelta(d.bars_after_v9*15,unit='min')).all()
    late = s.loc[s.arm.eq('joint_h1_recheck')]
    rejected = late.status.isin(('rejected_direction','rejected_unknown'))
    np.testing.assert_array_equal(rejected,~late.h1_pass)
    assert t.loc[t.arm.eq('joint_h1_recheck'),'h1_pass'].all()
    entered_keys = set(zip(t.arm,t.trade_key))
    closed = t.loc[~t.censored].copy()
    assert np.isfinite(closed[['gross_return','net_return','gross_r','net_r','initial_risk_frac']]).all().all()
    assert closed.initial_risk_frac.gt(0).all()
    np.testing.assert_allclose(closed.gross_return-closed.net_return,.002,atol=1e-12)
    np.testing.assert_allclose(closed.net_r,closed.net_return/closed.initial_risk_frac,atol=1e-10)
    np.testing.assert_allclose(closed.gross_r,closed.gross_return/closed.initial_risk_frac,atol=1e-10)
    np.testing.assert_allclose(closed.gross_return,(closed.exit_price-closed.entry_price)/closed.entry_price,atol=1e-12)
    np.testing.assert_allclose(t.initial_risk_frac,t.initial_risk/t.entry_price,atol=1e-12)
    joined=t.merge(d[['trade_key','next_open_time','next_open_price']],on='trade_key',validate='many_to_one')
    assert pd.to_datetime(joined.entry_time,utc=True).eq(pd.to_datetime(joined.next_open_time,utc=True)).all()
    np.testing.assert_allclose(joined.entry_price,joined.next_open_price,atol=1e-12)
    for _,g in t.groupby(['stream_symbol','arm']):
        ordered=g.sort_values('signal_i')
        assert (ordered.signal_i.to_numpy()[1:] >= ordered.exit_i.to_numpy()[:-1]).all()
    a=t.loc[t.arm.eq('baseline')].set_index('trade_key')
    b=t.loc[t.arm.eq('joint_h1_recheck')].set_index('trade_key')
    shared=a.index.intersection(b.index)
    cols=['entry_price','initial_stop','initial_risk','initial_risk_frac','exit_price','gross_return','net_return','net_r','exit_i']
    np.testing.assert_allclose(a.loc[shared,cols],b.loc[shared,cols],atol=1e-12,equal_nan=True)
    assert a.loc[shared,'exit_reason'].equals(b.loc[shared,'exit_reason'])
    assert set(zip(c.arm,c.trade_key)) == entered_keys
    assert not c.duplicated(['arm','trade_key']).any()
    assert c.loc[c.matched,['control_net_r','control_net_return']].notna().all().all()
    stats={
        'status':'pass','symbols':len(identity['symbols']),'candidate_rows':len(d),'status_rows':len(s),
        'trade_rows':len(t),'closed_rows':len(closed),'control_rows':len(c),'shared_executions':len(shared),
        'candidate_h1_rejected':int((~d.h1_pass).sum()),
        'source_hashes_verified':len(identity['source']),
        'identity_sha256':sha(root/'identity.json'),'manifest_sha256':sha(root/'manifest.json'),
        'audit_source_sha256':sha(Path(__file__)),
        'checks':['receipt_and_leaf_hashes','full_universe','unique_joint_per_box','two_complete_status_books',
            'h1_direction_and_available_clock','reference_break_and_fill_chronology','serial_no_overlap',
            '20bp_cost','R_denominator','long_price_return','same_event_same_execution','control_inventory'],
        'limitations':['no_native_TradingView_pivot_parity','H1 numerical implementation covered by causal synthetic checks; this audit checks saved direction and timestamps',
            'event_R_is_not_account_equity']}
    if output.exists():
        raise ValueError('refuse to overwrite an audit')
    output.write_text(json.dumps(stats,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(stats,ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    audit(args.input,args.output)
