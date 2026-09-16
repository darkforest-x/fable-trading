"""Break-even-with-cost, no-partial, gate algebra and exact fee recomputation."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import bb_stoch_longrun_study as study
from yoyo.evaluation.bb_stoch_parameter_replay import ParamSpec, prepare, replay_entry


def fixture(dip_low=100.10, n=210):
    """Flat bars, one long at 200, a reachable target and then a shallow dip."""
    index = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    frame = pd.DataFrame(dict(open=100.0, high=100.05, low=99.95, close=100.0,
                              signal=0, target_upper=101.0, target_lower=1.0), index=index)
    frame.iloc[200, frame.columns.get_loc("signal")] = 1
    for column, value in (("high", 101.5), ("low", 100.0), ("close", 101.0)):
        frame.iloc[202, frame.columns.get_loc(column)] = value
    for column, value in (("open", 100.5), ("high", 100.6), ("low", dip_low), ("close", 100.5)):
        frame.iloc[203, frame.columns.get_loc(column)] = value
    # Afterwards the market sits above both protections, so any exit below is
    # the dip on bar 203 and not a later drift through the level.
    for column, value in (("open", 100.5), ("high", 100.6), ("low", 100.35), ("close", 100.5)):
        frame.iloc[204:, frame.columns.get_loc(column)] = value
    return frame


def replay(frame, **spec):
    prepared = prepare(frame, ParamSpec(**spec))
    return replay_entry(prepared, 200)


def test_break_even_with_cost_protects_above_entry_and_exits_there():
    plain = replay(fixture())
    costed = replay(fixture(), be_cost_fraction=0.002)
    assert plain["partial"] and costed["partial"]
    # The same dip leaves the classic runner alone but clears the costed one,
    # and it leaves at entry + 0.2% rather than flat.
    assert plain["exit_reason"] == "boundary_censor"
    assert costed["exit_reason"] == "break_even"
    assert costed["exit_price"] == pytest.approx(100.0 * 1.002)
    assert costed["gross_pnl"] == pytest.approx(0.5 * 1.0 + 0.5 * 0.2)


def test_costed_break_even_is_reached_before_the_old_one_not_after():
    deep = replay(fixture(dip_low=99.90), be_cost_fraction=0.002)
    shallow = replay(fixture(dip_low=100.10), be_cost_fraction=0.002)
    assert deep["exit_price"] == shallow["exit_price"] == pytest.approx(100.2)
    assert deep["exit_i"] == shallow["exit_i"] == 203


def test_no_partial_ignores_the_band_target_entirely():
    frame = fixture()
    frame.iloc[205, frame.columns.get_loc("signal")] = -1
    row = replay(frame, partial_fraction=0.0, be_cost_fraction=0.002)
    # The band target at 101.0 was touched on bar 202 and must not have fired.
    assert not row["partial"] and row["tp_i"] is None
    assert [f["phase"] for f in row["fills"]] == ["entry", "full"]
    assert row["exit_reason"] == "opposite_signal_close" and row["exit_i"] == 205
    assert replay(frame)["partial"]


def test_defaults_are_untouched_by_the_new_parameters():
    default = ParamSpec()
    assert default.partial_fraction == 0.5 and default.be_cost_fraction == 0.0
    with pytest.raises(ValueError):
        ParamSpec(be_cost_fraction=0.03)
    with pytest.raises(ValueError):
        ParamSpec(partial_fraction=1.0)


def test_gates_only_remove_and_the_window_is_causal():
    n = 60
    index = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    frame = pd.DataFrame(dict(open=100., high=100.1, low=99.9, close=100.,
                              signal=0, target_upper=101., target_lower=99.), index=index)
    signals = np.zeros(n, dtype=int)
    signals[[10, 20, 30]] = [1, -1, 1]
    frame["signal"] = signals
    frame["rsi"] = 50.
    frame.iloc[10, frame.columns.get_loc("rsi")] = 25.
    frame.iloc[20, frame.columns.get_loc("rsi")] = 75.
    cfg = dict(rsi_length=14, rsi_lower=30., rsi_upper=70., sar_start=.02,
               sar_increment=.02, sar_maximum=.2, sar_window_bars=20)
    gates, _ = study.build_gates(frame, signals, cfg)
    assert gates["rsi_line"].tolist() == np.where(np.isin(np.arange(n), [10, 20]), signals, 0).tolist()
    for name, gate in gates.items():
        keep = gate != 0
        assert np.array_equal(gate[keep], signals[keep]) and not keep[signals == 0].any()


def test_fee_curve_recomputes_exactly_and_reports_a_negative_breakeven_when_gross_loses():
    rows = [dict(gross_pnl=-10.0, fees=2.0, initial_risk=50.0),
            dict(gross_pnl=4.0, fees=2.0, initial_risk=50.0)]
    curve = study.fee_curve(rows, [0.0, 0.001])
    assert curve[0]["net_pnl"] == pytest.approx(-6.0)
    assert curve[1]["net_pnl"] == pytest.approx(-10.0)
    assert curve[1]["net_r"] == pytest.approx((-10.0 - 2.0) / 50 + (4.0 - 2.0) / 50)
    star = curve[-1]
    assert star["fee_rate"] == "breakeven_f_star" and star["value"] < 0


def test_run_arm_skips_signals_while_a_position_is_open():
    frame = fixture()
    frame.iloc[201, frame.columns.get_loc("signal")] = 1
    prepared = prepare(frame, ParamSpec())
    entry_signal = frame.signal.to_numpy(int)
    context = dict(valid=np.ones(len(frame), dtype=bool))
    rows = study.run_arm(frame, prepared, context, entry_signal, "tv")
    assert [r["signal_i"] for r in rows] == [200]
