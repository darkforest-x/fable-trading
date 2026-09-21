"""The finite-search gate controls admission, never raw reversals or prices."""
from dataclasses import dataclass
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.spike_10r_serial import apply_gate

@dataclass
class Prepared:
    allowed: np.ndarray
    raw_side: np.ndarray
    frame: pd.DataFrame
    ordinal: dict
    context: object


def example():
    stamps=pd.date_range('2025-01-01',periods=3,freq='h',tz='UTC')
    return Prepared(np.array([True,True,True]),np.array([1,-1,1]),pd.DataFrame(index=stamps),
                    dict(zip(stamps,[101,102,103])),SimpleNamespace(key='x'))


def test_boolean_original_ordinal_gate_preserves_short_and_raw_exits():
    p=example()
    out=apply_gate(p,{'x:101:1':True,'x:103:1':False})
    assert out.allowed.tolist()==[True,True,False]
    assert p.allowed.all()
    assert out.raw_side is p.raw_side and out.frame is p.frame


def test_missing_rejects_and_string_false_is_not_truthy_permission():
    p=example()
    assert apply_gate(p,{}).allowed.tolist()==[False,True,False]
    with pytest.raises(ValueError,match='nonboolean'):
        apply_gate(p,{'x:101:1':'False'})


def test_serial_recall_excludes_newly_opened_winners_from_retention():
    from yoyo.evaluation.spike_10r_serial import serial_retention
    old=pd.DataFrame(dict(event_key=['a','b','c'],net_r=[12.,15.,-1.],valid_entry=True,censored=False))
    new=pd.DataFrame(dict(event_key=['a','d','e'],net_r=[12.,13.,14.],valid_entry=True,censored=False))
    got=serial_retention(new,old)
    assert got==dict(retained_gt10=1,lost_gt10=1,gained_gt10=2,recall=.5,gt10_count_ratio=1.5)
