"""Native tranche accounting and entry-time key snapshots for V2.30 exits."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.range_v230_exit_compare import (
    aggregate_legs, entry_keys, make_legs, oracle,
)
from yoyo.evaluation.pepe_range_breakeven import native
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def execute(bars, arm, side=1, keys=(90., 110.), risk=1.):
    bars = np.ascontiguousarray(bars, dtype=float)
    if side == -1:
        bars = np.ascontiguousarray(200-bars[:, [0, 2, 1, 3]])
    frame = pd.DataFrame(bars, columns=["open", "high", "low", "close"],
        index=pd.date_range("2026-01-01", periods=len(bars), freq="5min", tz="UTC"))
    base = pd.DataFrame([dict(trade_id="s", entry_i=0, side=side, entry=100.,
        stop=100-side*risk, risk=risk, end_i=len(bars)-1)])
    legs = make_legs(base, arm, {"s": keys}, .01)
    ctx = dict(bars=bars, atr=np.ones(len(bars))*.1, hour=np.zeros(len(bars)))
    rows, fills, audit = run_native(frame, ctx["atr"], ctx["hour"], legs,
        ExitPolicy(target_from_entry=True, opposite_hour_exit=False,
            breakeven_r=arm.get("be_r"),
            breakeven_includes_cost=arm.get("be_mode", "cost") == "cost"),
        tick=.01, symbol="TEST")
    conservative, check = oracle(ctx, legs, arm, .01, .002, native(), rows)
    assert check["native_price_time_parity"]
    return rows, aggregate_legs(rows, base, .002).iloc[0], conservative, fills, audit


@pytest.mark.parametrize("side", [1, -1])
def test_half_stop_then_full_target_keeps_original_risk_and_fees(side):
    rows, parent, _, fills, _ = execute(
        [[100]*4, [100,100.4,99.4,100], [100,105.2,99.6,104]],
        dict(target_r=5, stop_mode="half_r"), side)
    assert sorted(rows.reason) == [1, 4]
    assert parent.gross_r == pytest.approx(2.25)
    assert parent.fee_r == pytest.approx(.2)
    assert parent.net_r == pytest.approx(2.05)
    assert len(fills) == 4


@pytest.mark.parametrize("side", [1, -1])
def test_two_targets_are_independent_original_halves(side):
    rows, parent, _, _, audit = execute(
        [[100]*4, [100,104,99.5,103]], dict(split_r=[1,3]), side)
    assert rows.reason.tolist() == [4, 4]
    assert parent.gross_r == pytest.approx(2.)
    assert parent.net_r == pytest.approx(1.8)
    assert audit["native_fixed_target_accounting_checked"] == 2


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("mode,price,net", [("price",100.,-.2), ("cost",100.2,0.)])
def test_be_uses_completed_close_next_bar_and_still_charges_fees(side,mode,price,net):
    rows, parent, _, _, _ = execute(
        [[100]*4, [100,102,99.5,101.5], [101.5,102,99.5,101]],
        dict(target_r=5, be_r=1, be_mode=mode), side)
    row = rows.iloc[0]
    assert row.exit_i == 2 and row.reason == 5
    assert row.be_effective_i == 1
    assert row.exit_price == pytest.approx(price if side==1 else 200-price)
    assert parent.net_r == pytest.approx(net)


def test_cost_be_defers_when_cost_price_is_above_current_price():
    rows, _, _, _, _ = execute(
        [[100]*4, [100,100.15,99.95,100.1], [100.1,100.3,100,100.25],
         [100.25,100.3,100.15,100.25]],
        dict(target_r=5,be_r=1,be_mode="cost"),risk=.1)
    row=rows.iloc[0]
    assert row.be_trigger_i==1 and row.be_effective_i==2
    assert row.deferred_closes==1 and row.exit_i==3


def test_missing_key_target_does_not_enable_atr_trailing():
    rows, parent, _, _, _ = execute(
        [[100]*4, [100,106,99.5,106], [106,106,100,101]],
        dict(key_tp=True),keys=(90.,95.))
    assert rows.reason.tolist() == [0,0]
    assert rows.arm_i.tolist() == [-1,-1]
    assert parent.missing_target_weight == 1
    assert parent.boundary_weight == 1


def test_same_bar_sensitivity_preserves_original_halves():
    rows, parent, lower, _, _ = execute(
        [[100]*4,[100,106,98,100]],dict(split_r=[1,3]))
    assert parent.gross_r == pytest.approx(2.)
    assert lower.reason.tolist()==[1,1]
    assert lower.exit_price.tolist()==[99.,99.]


def test_key_targets_sorted_and_early_stop_strictly_inside_original_stop():
    base=pd.DataFrame([dict(trade_id="s",entry=100.,side=1,stop=98.,risk=2.)])
    arm=dict(key_tp=True,stop_mode="key")
    legs=make_legs(base,arm,{"s":[103,101,101,99,97,98,99.5,100]},.01)
    assert legs.target_price.tolist()==[101,103]
    assert legs.stop.tolist()==[99.5,98]
    no_mid=make_legs(base,arm,{"s":[103,101,97,98,100]},.01)
    assert no_mid.stop.tolist()==[98,98]


def test_entry_key_snapshot_includes_last_monday_close_and_ignores_future():
    frame=pd.DataFrame(dict(open=100.,high=110.,low=90.,close=100.),
        index=pd.date_range("2026-10-19 04:05", "2026-10-20 05:00",freq="5min",tz="UTC"))
    entry=frame.index.get_loc(pd.Timestamp("2026-10-20 04:00Z"))
    frame.iloc[entry,frame.columns.get_loc("high")]=120.
    base=pd.DataFrame([dict(trade_id="s",entry_i=entry)])
    expected=np.array([90.,120.,105.,123.9,86.1])
    for source in (frame,frame.iloc[:entry+1]):
        keys=entry_keys(source,base,[0,.5,1.13],"America/New_York")["s"]
        np.testing.assert_allclose(keys,expected)
    changed=frame.copy()
    changed.iloc[entry+1:,changed.columns.get_loc("high")]=99999
    np.testing.assert_allclose(
        entry_keys(changed,base,[0,.5,1.13],"America/New_York")["s"],expected)


def test_invalid_target_combinations_fail_closed():
    with pytest.raises(ValueError,match="Per-entry targets"):
        ExitPolicy(target_from_entry=True,fixed_target_r=5)
