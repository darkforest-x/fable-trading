"""Synthetic study accounting and admission boundaries; no market reads."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_v9_cost_be2_study as study


def test_missing_approval_fails_before_universe_read(tmp_path, monkeypatch):
    monkeypatch.setattr(study,'EXP',tmp_path)
    with pytest.raises(FileNotFoundError):
        study.verify_approval({'strategy_version':'unapproved'},{})


def test_fill_audit_uses_both_cost_legs_and_exit_quantity():
    trades=pd.DataFrame([dict(trade_id='one',censored=False,gross_return=.02,net_return=.018,
        net_r=1.8,initial_risk_frac=.01)])
    fills=pd.DataFrame([
        dict(trade_id='one',kind='entry',price=100.,qty_fraction=1.,side=1,cost_return=.001),
        dict(trade_id='one',kind='exit',price=102.,qty_fraction=1.,side=1,cost_return=.001)])
    assert study.audit_fills(trades,fills)['closed_checked']==1
    fills.loc[1,'cost_return']=0.
    with pytest.raises(AssertionError):study.audit_fills(trades,fills)


def test_event_identity_uses_stream_and_side():
    table=pd.DataFrame(dict(stream_key=['a','a','b'],signal_i=[7,7,7],side=[1,-1,1]))
    assert study.event_key(table).nunique()==3


def test_study_prep_keeps_raw_reversal_outside_entry_clock():
    idx=pd.date_range('2026-01-03T23:45Z',periods=8,freq='15min')
    frame=pd.DataFrame({c:np.full(8,v) for c,v in {'open':100.,'high':101.,'low':99.,'close':100.,'atr':1.,'rv':2.,'ready':True}.items()},index=idx)
    raw=pd.DataFrame({'long_signal':[False,True,False,False,False,False,False,False],
                      'short_signal':[False,False,True,False,False,False,False,False]},index=idx)
    mask=raw.any(axis=1)
    p=study.eth_prepared(frame,raw,mask,15,pd.Timestamp('2026-01-03T00:00Z'),pd.Timestamp('2026-01-04T02:00Z'),{'tick':.01})
    assert not p.allowed.any()  # Both scheduled opens fall on UTC Sunday.
    assert p.raw_side[2]==-1   # The filtered opposite event remains an exit.


def test_parity_rejects_changed_exit_even_with_same_sum():
    rows=[]
    for i in [0,1]:
        item={c:float(i+1) for c in study.PARITY}
        item.update(signal_i=i,side=1,exit_reason='initial_stop',censored=False)
        rows.append(item)
    original=pd.DataFrame(rows);changed=original.copy()
    changed['net_r']=changed.net_r.iloc[::-1].to_numpy()
    with pytest.raises(AssertionError):study.assert_parity(changed,original)
