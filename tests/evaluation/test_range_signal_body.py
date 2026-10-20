"""Current-candle admission and outcome-blind rematching of frozen trades."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.range_signal_body import body_gate, pair_rows


def test_body_direction_and_doji():
    frame = pd.DataFrame(dict(open=[100]*6, close=[101,99,100,101,99,100]))
    rows = pd.DataFrame(dict(entry_i=range(6), side=[1,1,1,-1,-1,-1]))
    np.testing.assert_array_equal(body_gate(rows, frame), [True,False,False,False,True,False])


def test_gate_ignores_future_candles_and_outcomes():
    rows = pd.DataFrame(dict(entry_i=[1,2], side=[1,-1], net_r=[999,-999]))
    frame = pd.DataFrame(dict(open=[100]*5, close=[99,101,99,100,101]))
    prefix = body_gate(rows, frame.iloc[:3])
    frame.loc[3:, "close"] = -100
    np.testing.assert_array_equal(prefix, body_gate(rows.assign(net_r=0), frame))
    with pytest.raises(ValueError, match="outside"):
        body_gate(rows, frame.iloc[:2])


def test_pairing_has_same_strata_unique_controls_and_no_outcome_input():
    s = pd.DataFrame(dict(trade_id=["s0","s1","s2"], pair_id=[0,1,2], segment=[0]*3,
        week=[1,1,2], side=[1]*3, bucket=[2]*3, entry_i=[10,20,30],
        net_r=[-1,3,20], net_notional_bp=[-2,4,30]))
    r = s.iloc[:2].copy()
    r["trade_id"] = ["r0","r1"]
    r["entry_i"] = [12,18]
    p = pair_rows(s,r)
    assert len(p)==2 and p.control_id.nunique()==2
    changed = pair_rows(s.assign(net_r=999, net_notional_bp=999),
                        r.assign(net_r=-999, net_notional_bp=-999))
    pd.testing.assert_frame_equal(p[["trade_id","control_id"]],
                                  changed[["trade_id","control_id"]])
