"""Archived all-day parity must catch outcome and admission drift."""
import pandas as pd
import pytest

from yoyo.evaluation.spike_v8_ict_multitf import assert_same_trades


def row():
    return dict(signal_i=1,entry_i=2,exit_i=5,side=1,entry_price=100.,initial_stop=98.,exit_price=104.,
                gross_r=2.,net_r=1.9,exit_reason='fanshen_alerts_next_open',censored=False,exit_at_open=True,
                entry_time='2026-01-01T03:00:00Z',exit_time='2026-01-01T03:15:00Z')


def test_archived_parity_accepts_equivalent_timestamp_serialization():
    a=row();b=dict(a,entry_time='2026-01-01 03:00:00+00:00')
    assert assert_same_trades([a],pd.DataFrame([b]))['passed']


@pytest.mark.parametrize('field,value',[('net_r',1.8),('signal_i',0),('exit_at_open',False),('censored',True)])
def test_archived_parity_rejects_semantic_drift(field,value):
    a=row();b=dict(a);b[field]=value
    with pytest.raises(AssertionError):assert_same_trades([a],pd.DataFrame([b]))


def test_archived_parity_cannot_drop_a_trade():
    with pytest.raises(AssertionError):assert_same_trades([],pd.DataFrame([row()]))
