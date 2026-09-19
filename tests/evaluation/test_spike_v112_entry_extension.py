"""Causal prefix, stop geometry, and serial-state tests for the fixed E<=1 gate."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_v112_entry_extension as entry
from yoyo.evaluation.spike_v7_fast import ExecutionSpec, _initial_position_fast


def prepared():
    n=20
    return SimpleNamespace(frame=pd.DataFrame(index=pd.date_range("2025-01-01",periods=n,freq="h",tz="UTC")),
        open=np.full(n,100.),high=np.full(n,101.),low=np.full(n,99.),close=np.full(n,100.),
        atr=np.ones(n),gap=np.zeros(n,bool),spec=ExecutionSpec(tick=.1))


def test_signal_geometry_matches_frozen_stop_but_ignores_entry_open():
    p=prepared();p.low[3]=96.37;p.open[7]=105.
    stop,risk=entry.parent_geometry(p,6)
    original=_initial_position_fast(p.frame.index,p.open,p.high,p.low,p.close,p.atr,p.gap,6,1,p.spec)
    assert stop==original["initial_stop"]
    assert stop==pytest.approx(96.1)
    assert risk==pytest.approx(3.9)
    assert original["initial_risk"]==pytest.approx(8.9)
    p.low[1]=1.  # Outside the five-bar anchor window.
    assert entry.parent_geometry(p,6)==(stop,risk)


def test_boundary_negative_and_zero_wait_are_explicit():
    p=prepared()
    assert entry.features_at(p,6,6)["extension"]==0
    assert entry.features_at(p,6,6)["extension_pass"]
    for close,expected,passed in [(99.,-.5,True),(102.,1.,True),(102.001,1.0005,False)]:
        p.close[9]=close
        actual=entry.features_at(p,6,9)
        assert actual["extension"]==pytest.approx(expected)
        assert actual["extension_pass"] is passed


def test_future_suffix_and_next_open_do_not_change_features():
    p=prepared();p.close[9]=101.
    before=entry.features_at(p,6,9)
    p.open[7]=999.;p.open[10]=.1
    for field in ("open","high","low","close","atr"):
        getattr(p,field)[10:]=np.nan
    p.gap[10:]=True
    assert entry.features_at(p,6,9)==before


@pytest.mark.parametrize("bad",["nan","gap","zero_atr","bad_ohlc","negative_stop"])
def test_invalid_anchor_or_gap_fails_closed(bad):
    p=prepared()
    if bad=="nan":p.atr[6]=np.nan
    elif bad=="gap":p.gap[8]=True
    elif bad=="zero_atr":p.atr[6]=0
    elif bad=="bad_ohlc":p.low[5]=101.
    else:p.atr[6]=100.
    actual=entry.features_at(p,6,9)
    assert not actual["extension_pass"]
    assert actual["gate_reason"]=="invalid_geometry_or_gap"


def test_efficiency_is_six_changes_and_flat_is_zero():
    p=prepared()
    p.close[3:10]=[100.,102.,101.,103.,102.,104.,103.]
    assert entry.features_at(p,6,9)["efficiency6"]==pytest.approx(3/9)
    p.close[3:10]=100.
    assert entry.features_at(p,6,9)["efficiency6"]==0
    p.gap[4]=True
    assert np.isnan(entry.features_at(p,6,9)["efficiency6"])


def test_rejected_first_break_cannot_retry_same_box():
    active=np.zeros(12,bool);active[2:10]=True
    parent=np.where(active,2,-1)
    breaks=np.zeros(12,bool);breaks[[4,7]]=True
    candidates=entry.box_joints(active,parent,breaks)
    passed=np.zeros(12,bool);passed[7]=True
    assert np.flatnonzero(candidates).tolist()==[4]
    assert not (candidates&passed).any()


def test_independent_serial_frees_candidates_and_preserves_event_keys(monkeypatch):
    def attempt(_prepared,i):
        return "closed",{"signal_i":i,"exit_i":10 if i==1 else i+1,"net_r":float(i)}
    monkeypatch.setattr(entry.support.inc,"attempt",attempt)
    p=prepared();candidates=np.array([1,5,12])
    a,sa=entry.support.serial(p,candidates,"stream",entry.ARMS[0])
    b,sb=entry.support.serial(p,candidates,"stream",entry.ARMS[1])
    assert [{k:v for k,v in row.items() if k!="arm"} for row in a]==[{k:v for k,v in row.items() if k!="arm"} for row in b]
    assert [{k:v for k,v in row.items() if k!="arm"} for row in sa]==[{k:v for k,v in row.items() if k!="arm"} for row in sb]
    freed,_=entry.support.serial(p,np.array([5,12]),"stream",entry.ARMS[1])
    assert [r["signal_i"] for r in a]==[1,12]
    assert [r["signal_i"] for r in freed]==[5,12]
    assert freed[-1]["trade_key"]==a[-1]["trade_key"]=="stream:box_any:12"


def test_month_bootstrap_uses_each_arms_denominator():
    a=pd.DataFrame({"month":["a","a","b"],"net_r":[1.,-1.,2.]})
    b=pd.DataFrame({"month":["a","b"],"net_r":[1.,2.]})
    result=entry.mean_difference(a,b,"net_r")
    assert result["delta"]==pytest.approx(1.5-2/3)
    assert result["blocks"]==2
    assert result["low"]<=result["delta"]<=result["high"]
