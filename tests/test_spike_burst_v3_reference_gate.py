"""Causal occupancy and actual V3 parent/child regeneration, no future outcomes."""
import numpy as np
import pandas as pd
from yoyo.evaluation import spike_burst_v3_reference_gate as rg
from yoyo.evaluation import spike_burst_early_warning as ew


def frame(n=100):
    f = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=100.,
        atr=1., tr=2., ready=True, history_count=np.arange(n)+400, atr_pct=.01,
        s20=99., e20=99.5, s60=100., e60=100., s120=100., e120=100.,
        ropeHigh=101., pastWidth=1., pastCrosses=3.,
        middle=100.+np.arange(n)*.01, md=1., sb=0., rv=1., expansion=1.),
        index=pd.date_range("2026-07-10T00:00Z", periods=n, freq="h"))
    f.attrs["minutes"] = 60
    return f


def launch(f, i, c=104., volume=600.):
    f.loc[f.index[i], ["open", "high", "low", "close", "volume"]] = [100., c+.2, 99., c, volume]


def test_reference_suppresses_without_future_exit_or_resetting_risk():
    f=frame()
    launch(f,30); launch(f,42,106.); launch(f,54,108.)
    base=ew.detect(f); s=rg.detect(f,.01)
    assert np.flatnonzero(base.early).tolist()==[30,42,54]
    assert np.flatnonzero(s.early).tolist()==[30]
    assert s.holding_blocked.iloc[[42,54]].all()
    assert s.reference_entry.iloc[54]==104.
    assert s.confirmed.iloc[30] and s.reference_started.sum()==1


def test_prefix_and_future_mutation_leave_all_past_columns_unchanged():
    f=frame()
    for i in (30,42,56,70): launch(f,i,104+i/10)
    s=rg.detect(f,.01)
    for n in (29,31,34,43,57,71,99):
        pd.testing.assert_frame_equal(rg.detect(f.iloc[:n],.01),s.iloc[:n])
    g=f.copy(); g.iloc[75:,g.columns.get_loc("low")]=1.
    pd.testing.assert_frame_equal(rg.detect(g,.01).iloc[:75],s.iloc[:75])


def test_stop_bar_cannot_restart_or_count_its_favorable_high():
    f=frame(); launch(f,30)
    launch(f,42,120.); f.loc[f.index[42],"low"]=90.
    launch(f,43,121.)
    launch(f,45,125.)
    s=rg.detect(f,.01)
    assert s.reference_exit.iloc[42] and not s.early.iloc[42]
    assert not s.early.iloc[43]  # Raw condition stayed true, no artificial edge.
    assert s.early.iloc[45]  # Blocked edge did not restart the cooldown.
    assert s.reference_peak_r.iloc[42]==s.reference_peak_r.iloc[41]


def test_reference_gap_censored_and_no_boundary_mark_unlock():
    f=frame(); launch(f,30)
    s=rg.detect(f,.01)
    assert s.reference_active.iloc[-1] and not s.reference_exit.any()
    g=f.drop(f.index[41]); launch(g,55,106.)
    x=rg.detect(g,.01)
    assert x.reference_gap_censored.iloc[41] and not x.reference_active.iloc[41]
    assert x.early.iloc[55]


def test_invalid_risk_does_not_remove_parent_or_cooldown():
    f=frame(); launch(f,30)
    f.loc[f.index[30],"atr"]=100.
    launch(f,35,106.); launch(f,45,108.)
    s=rg.detect(f,.01)
    assert s.early.iloc[30] and not s.reference_started.iloc[30]
    assert not s.early.iloc[35] and s.cooldown_blocked.iloc[35]
    assert s.early.iloc[45]


def test_child_after_exit_is_upgrade_not_new_reference():
    f=frame(); launch(f,30,101.1,100.)
    f.loc[f.index[30],"high"]=110.
    for i in (31,32): launch(f,i,101.1,100.)
    f.loc[f.index[31],"low"]=90.
    launch(f,33,104.,600.)
    s=rg.detect(f,.01)
    assert s.reference_exit.iloc[31] and s.confirmed.iloc[33]
    assert s.confirm_age.iloc[33]==3
    assert not s.reference_active_at_confirmation.iloc[33]
    assert s.reference_started.sum()==1
