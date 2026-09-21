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
