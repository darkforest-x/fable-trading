"""Native touch-to-entry protection; synthetic paths never imply observed ticks."""
import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def execute(bars, side=1, path="OHLC", target=2., be=True, entries=None):
    bars = np.asarray(bars, dtype=float)
    if side == -1:
        bars = 200-bars[:, [0, 2, 1, 3]]
    f = pd.DataFrame(bars, columns=["open", "high", "low", "close"],
        index=pd.date_range("2026-01-01", periods=len(bars), freq="5min", tz="UTC"))
    if entries is None:
        entries = pd.DataFrame([dict(trade_id="s", entry_i=0, side=side,
            entry=100., stop=100.-side, risk=1., end_i=len(bars)-1)])
    p = ExitPolicy(fixed_target_r=target, opposite_hour_exit=False,
        breakeven_r=1. if be else None, breakeven_includes_cost=False,
        breakeven_on_touch=be, intrabar_path=path)
    rows, fills, audit = run_native(f, np.ones(len(f)), np.zeros(len(f)), entries, p, .01, "TEST")
    assert len(fills) == 2*len(entries)
    return rows, audit


@pytest.mark.parametrize("side,path", [(1,"OHLC"), (-1,"OLHC")])
def test_wick_arms_before_same_bar_return_to_entry(side,path):
    r,_ = execute([[100]*4,[100,101.5,99.5,100.5],[100.5]*4],side,path)
    x=r.iloc[0]
    assert x.reason==5 and x.exit_i==1 and x.exit_price==100
    assert x.be_trigger_i==x.be_effective_i==1
    assert x.gross_r==0 and x.net_r==pytest.approx(-.2)


@pytest.mark.parametrize("side,path", [(1,"OLHC"), (-1,"OHLC")])
def test_adverse_extreme_before_activation_cannot_stop_at_future_be(side,path):
    r,_=execute([[100]*4,[100,101.5,99.5,101.2],[101.2,101.3,99.8,100.5]],side,path)
    assert r.iloc[0].reason==5 and r.iloc[0].exit_i==2


@pytest.mark.parametrize("side,path", [(1,"OLHC"), (-1,"OHLC")])
def test_initial_stop_before_later_favorable_wick(side,path):
    r,_=execute([[100]*4,[100,101.5,98,100.5],[100.5]*4],side,path)
    x=r.iloc[0]
    assert x.reason==1 and x.exit_price==100-side and x.be_trigger_i==-1


@pytest.mark.parametrize("side,path", [(1,"OHLC"), (-1,"OLHC")])
def test_one_r_target_precedes_redundant_one_r_be(side,path):
    r,_=execute([[100]*4,[100,102,98,100.5],[100.5]*4],side,path,target=1.)
    x=r.iloc[0]
    assert x.reason==4 and x.exit_price==100+side and x.be_trigger_i==-1


@pytest.mark.parametrize("side,path", [(1,"OHLC"), (-1,"OLHC")])
def test_real_open_gap_after_be_fills_adverse_open(side,path):
    r,_=execute([[100]*4,[100,101.5,100.5,101.2],[98.5,102,98,100]],side,path)
    x=r.iloc[0]
    assert x.reason==5 and x.exit_i==2 and x.exit_price==100-side*1.5
    assert x.gross_r==pytest.approx(-1.5)


def test_entry_bar_extreme_never_arms_or_exits_new_close_entry():
    r,_=execute([[100,150,50,100],[100,100.5,99.5,100.2],[100.2]*4])
    assert r.iloc[0].reason==0 and r.iloc[0].be_trigger_i==-1


def test_shared_quote_multiple_positions_keep_their_own_be():
    entries=pd.DataFrame([dict(trade_id=f"s{i}",entry_i=0,side=side,entry=100.,
        stop=100.-side*risk,risk=risk,end_i=2) for i,(side,risk) in enumerate([(1,1.),(1,2.),(-1,1.)])])
    r,_=execute([[100]*4,[100,101.5,99.5,100.5],[100.5,104.5,100.3,104]],entries=entries)
    out=r.set_index("trade_id")
    assert out.loc['s0','reason']==5 and out.loc['s0','exit_price']==100
    assert out.loc['s1','reason']==4 and out.loc['s1','exit_price']==104
    assert out.loc['s2','reason']==1 and out.loc['s2','exit_price']==101


def test_touch_contract_rejects_unsupported_cost_protection():
    with pytest.raises(ValueError, match="entry-price"):
        ExitPolicy(fixed_target_r=2,opposite_hour_exit=False,breakeven_r=1,
                   breakeven_on_touch=True,intrabar_path="OHLC")


@pytest.mark.parametrize("path",["OHLC","OLHC"])
@pytest.mark.parametrize("side",[1,-1])
@pytest.mark.parametrize("target",[1,2,3])
@pytest.mark.parametrize("be",[False,True])
def test_signed_path_oracle_matches_native_with_multiple_gaps(path,side,target,be):
    from yoyo.evaluation.range_v237_fixed_exits import oracle
    bars=np.array([[100,150,50,100],[100,100.8,99.3,100.5],
        [100.5,101.4,99.7,100.9],[101.5,103.2,99.8,101.2],
        [98.5,102,97.5,100]],float)
    rows,_=execute(bars,side,path,target,be)
    if side==-1:bars=200-bars[:,[0,2,1,3]]
    expected=oracle(bars,rows,target,be,path,.01)[0]
    assert rows.iloc[0].exit_i==expected[0] and rows.iloc[0].reason==expected[2]
    assert rows.iloc[0].exit_price==pytest.approx(expected[1])


@pytest.mark.parametrize("side",[1,-1])
@pytest.mark.parametrize("target",[1,2,3])
def test_segmented_ohlc_preserves_original_fixed_target_kernel(side,target):
    bars=[[100]*4,[100,100.8,99.4,100.5],[100.5,104,98,100],[100]*4]
    ordinary,_=execute(bars,side,None,target,False)
    segmented,_=execute(bars,side,"OHLC",target,False)
    for col in ['exit_i','exit_price','reason','gross_r','net_r','framework_commission']:
        assert ordinary.iloc[0][col]==pytest.approx(segmented.iloc[0][col])
