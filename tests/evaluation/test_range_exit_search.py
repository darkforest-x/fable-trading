"""Native resting targets, sibling cancellation and parameter-search causality."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.pepe_range_breakeven import replay_search, assert_equal
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def check(bars, side=1, atr=None, hour=None, **params):
    bars = np.ascontiguousarray(bars, dtype=float)
    n = len(bars)
    ctx = dict(bars=bars, atr=np.array([np.nan]*n if atr is None else atr),
               hour=np.array([0.]*n if hour is None else hour, dtype=float))
    frame = pd.DataFrame(bars, columns=["open","high","low","close"],
        index=pd.date_range("2026-01-01",periods=n,freq="5min",tz="UTC"))
    source = dict(trade_id="s",entry_i=0,side=side,entry=100.,
                  stop=100.-side,risk=1.,end_i=n-1)
    policy = ExitPolicy(fixed_target_r=params.get("target_r"),
        breakeven_r=params.get("be_r"),trail_arm_r=params.get("arm_r",5.),
        trail_atr_multiple=params.get("atr_multiple",4.),
        opposite_hour_exit=params.get("reverse_exit",True))
    rows,fills,audit = run_native(frame,ctx["atr"],ctx["hour"],
        pd.DataFrame([source]),policy,.01,"TEST")
    actual = rows.iloc[0]
    expected = replay_search(ctx,[[0,side,100.,100.-side,n-1]],**params)[0]
    fields = ["exit_i","exit_price","reason","peak_r","arm_i","protection",
              "ambiguous","steps","be_trigger_i","be_effective_i","stop_kind",
              "be_price","deferred_closes"]
    assert_equal(expected,actual[fields].to_numpy(float),.01)
    assert len(fills)==2 and fills.quantity.nunique()==1
    assert actual.framework_commission==pytest.approx(.002*100*actual.quantity)
    return actual, audit


@pytest.mark.parametrize("side",[1,-1])
@pytest.mark.parametrize("kind",["target","stop","collision","target_gap","stop_gap","boundary"])
def test_fixed_targets_match_native_path_and_cancel_sibling(side,kind):
    bar = {"target":[100,103,99.5,100.5],"stop":[100,100.5,98,100],
           "collision":[100,103,98,100],"target_gap":[104,105,98,100],
           "stop_gap":[98,103,97,100],"boundary":[100,101,99.5,100.5]}[kind]
    if side == -1:
        o,h,l,c = bar
        bar=[200-o,200-l,200-h,200-c]
    r,audit=check([[100,130,70,100],bar,[100,130,70,100]],side,target_r=2)
    if kind=="collision":
        assert r.reason==(4 if side==1 else 1)
        assert r.ambiguous==1
    elif kind in ("target","target_gap"):
        assert r.reason==4
    elif kind in ("stop","stop_gap"):
        assert r.reason==1
    assert audit["native_fixed_target_accounting_checked"]==1


def test_fixed_target_boundary_cancels_remaining_orders():
    r,_=check([[100]*4,[100,101,99.5,100.5]],target_r=20)
    assert r.reason==0 and r.arm_i==-1


@pytest.mark.parametrize("be_r",[1.,2.,3.])
def test_parameterized_fee_cover_uses_completed_management_close(be_r):
    r,_=check([[100]*4,[100,104,99.5,103],[103,104,102,103],[103,104,100,101]],
        atr=[np.nan,np.nan,1.,np.nan],hour=[0.,-1.,-1.,0.],
        be_r=be_r,target_r=20)
    assert r.be_trigger_i==2 and r.be_effective_i==2 and r.reason==5
    assert r.net_r==pytest.approx(0,abs=1e-8)


def test_reverse_can_be_disabled_without_changing_trailing_clock():
    r,_=check([[100]*4,[100,107,99.5,106],[106,107,104,106]],
        atr=[np.nan,2.,2.],hour=[0.,-1.,0.],reverse_exit=False)
    assert r.arm_i==1 and r.reason==0 and r.exit_i==2


def test_impossible_short_target_is_not_clipped_to_positive_price():
    r,_=check([[100]*4,[100,100.5,90,95]],side=-1,target_r=200)
    assert r.reason==0 and r.gross_r==5


def test_target_partial_combination_fails_closed():
    with pytest.raises(ValueError,match="cannot be combined"):
        ExitPolicy(fixed_target_r=3,partial_close_r=1)


def test_grid_and_development_selection_exclude_later_outcomes():
    import json
    from yoyo.evaluation.range_exit_search import DEFAULT,all_arms,choose
    arms=all_arms(json.loads(DEFAULT.read_text()))
    assert len(arms)==3520
    assert all(p["be_r"] is None or p["be_r"]<p["target_r"]
               for p in arms if p["family"]=="fixed")
    table=pd.DataFrame([
        dict(name="a",segment=0,selection_net_r=1,realized_r_drawdown=1),
        dict(name="b",segment=0,selection_net_r=2,realized_r_drawdown=1),
        dict(name="a",segment=1,selection_net_r=100000,realized_r_drawdown=1),
        dict(name="b",segment=1,selection_net_r=-100000,realized_r_drawdown=1)])
    assert choose(table,0)=="b"
