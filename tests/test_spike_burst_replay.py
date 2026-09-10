"""Frozen Pine replay checks: native probe cases plus adversarial temporal paths.

No market observations or future return scoring are used. Expected pure-helper
values are the same hand-worked cases executed by the native Pine probes. State
fixtures reproduce their input series; feature seeding uses independent sums.
"""

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.evaluation.build_spike_burst_probe import (
    BURST_CASES, PATH_CASES, PRICE_CASES, RISK_CASES, WINDOW_CASES,
)
from yoyo.evaluation.spike_burst_replay import (
    SOURCE_SHA256, burst, features, launch_window, path_reference, price_burst,
    replay, risk_reference, pine_gt, pine_ge, pine_lt, pine_le,
)


def _values(values):
    return tuple(float("nan") if value is None else value for value in values)


def _assert_values(actual, expected):
    for a, b in zip(actual, expected):
        if b is None:
            assert np.isnan(a)
        elif isinstance(b, bool):
            assert a == b
        else:
            assert a == pytest.approx(b, abs=1e-10)


def test_frozen_source_hash():
    source = Path(__file__).resolve().parents[1] / "yoyo/evaluation/pine/spike_burst_v1.pine"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == SOURCE_SHA256


@pytest.mark.parametrize("case", BURST_CASES, ids=lambda x: x.name)
def test_native_burst_cases(case):
    assert burst(*_values(case.values)) == case.passes


@pytest.mark.parametrize("case", PRICE_CASES, ids=lambda x: x.name)
def test_native_price_first_cases(case):
    assert price_burst(*_values(case.values)) == case.passes


@pytest.mark.parametrize("name,args,expected", WINDOW_CASES, ids=[x[0] for x in WINDOW_CASES])
def test_native_window_cases(name, args, expected):
    assert launch_window(*_values(args)) == expected


@pytest.mark.parametrize("name,args,expected", RISK_CASES, ids=[x[0] for x in RISK_CASES])
def test_native_risk_cases(name, args, expected):
    _assert_values(risk_reference(*_values(args)), expected)


@pytest.mark.parametrize("name,args,expected", PATH_CASES, ids=[x[0] for x in PATH_CASES])
def test_native_path_cases(name, args, expected):
    _assert_values(path_reference(*_values(args)), expected)


def _ohlcv(n=440):
    i = np.arange(n)
    close = 100 + np.sin(i / 7) + i / 100
    op = close - 0.2 * np.cos(i / 2)
    return pd.DataFrame(dict(open=op, high=np.maximum(op, close) + .5,
                             low=np.minimum(op, close) - .4, close=close,
                             volume=10 + i % 29),
                        index=pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC"))


def test_feature_initial_seeds_are_hand_worked_and_warmup_is_exact():
    source = _ohlcv()
    f = features(source)
    assert f.md.iloc[:33].eq(0).all()
    assert f.sb.iloc[:8].isna().all() and f.sb.iloc[8] == 0
    assert f.smHigh.iloc[:33].isna().all()
    assert f.smHigh.iloc[33] == pytest.approx(sum(source.high.iloc[:34]) / 34)
    assert f.smHigh.iloc[34] == pytest.approx((f.smHigh.iloc[33] * 33 + source.high.iloc[34]) / 34)
    tr = [source.high.iloc[0] - source.low.iloc[0]]
    for i in range(1, 15):
        tr.append(max(source.high.iloc[i] - source.low.iloc[i],
                      abs(source.high.iloc[i] - source.close.iloc[i-1]),
                      abs(source.low.iloc[i] - source.close.iloc[i-1])))
    assert f.atr.iloc[:13].isna().all()
    assert f.atr.iloc[13] == pytest.approx(sum(tr[:14]) / 14)
    assert f.atr.iloc[14] == pytest.approx((sum(tr[:14]) / 14 * 13 + tr[14]) / 14)
    assert f.e20.iloc[0] == source.close.iloc[0]
    assert f.e20.iloc[1] == pytest.approx(2 / 21 * source.close.iloc[1] + 19 / 21 * source.close.iloc[0])
    assert f.ropeHigh.iloc[:119].isna().all()
    assert f.pastWidth.iloc[:131].isna().all()
    assert f.pastWidth.iloc[131] == pytest.approx(f.width.iloc[119:131].mean())
    assert not f.ready.iloc[:340].any() and f.ready.iloc[340:].all()


def test_feature_prior_denominators_cannot_use_current_launch_bar():
    source = _ohlcv()
    f = features(source)
    changed = source.copy()
    changed.loc[changed.index[400], ["high", "volume"]] = [1000, 100000]
    g = features(changed)
    for column in ("pastWidth", "pastCrosses", "pastVolume"):
        assert f[column].iloc[400] == g[column].iloc[400]
    assert g.expansion.iloc[400] == pytest.approx(g.tr.iloc[400] / f.atr.iloc[399])
    assert g.rv.iloc[400] == pytest.approx(100000 / source.volume.iloc[380:400].median())


def test_missing_volume_is_not_fabricated_and_previous_median_skips_na():
    source = _ohlcv(80)
    source.iloc[5, source.columns.get_loc("volume")] = np.nan
    source.iloc[40, source.columns.get_loc("volume")] = np.nan
    f = features(source)
    assert np.isnan(f.pastVolume.iloc[20])
    assert f.pastVolume.iloc[21] == source.volume.iloc[:21].dropna().median()
    assert np.isnan(f.rv.iloc[40])
    assert f.pastVolume.iloc[41] == source.volume.iloc[:41].dropna().iloc[-20:].median()


def _state_fixture(scenario=0, n=30):
    # Same native state fixture: one preroll bar, then step0 is the first quiet.
    rows = []
    for i in range(n):
        b = i - 1
        row = dict(open=100., high=100.4, low=99.6, close=100., md=0., sb=0.,
                   atr=1.3 if b >= 12 else 1., rv=1., expansion=1., pastWidth=1.,
                   pastCrosses=3., ropeHigh=100.2, ropeLow=99.8, recentLow=99.,
                   recentHigh=101., ready=True)
        if 12 <= b <= 18:
            row.update(open=100., high=100.6, low=99.9, close=100.3, md=.11, sb=.05)
        if b == 13 and scenario == 3:
            row["md"] = .09
        if b == 14 or b == 18 and scenario == 2:
            row.update(open=100., high=104.2, low=99., close=104., md=.30, sb=.10,
                       rv=3.99 if scenario == 4 else 1. if scenario == 2 and b == 14 else 5., expansion=4.)
            if scenario == 5:
                row["atr"] = 100.
        if 15 <= b <= 17 and scenario == 0:
            row.update(open=104., high=120., low=104., close=115., md=.50, sb=.20, rv=5., expansion=4.)
            if b == 16:
                row.update(open=115., high=116., low=111., close=114.)
            if b == 17:
                row.update(open=105., high=130., low=104., close=120.)
        if scenario == 6 and 15 <= b <= 26:
            row.update(open=104., high=104.2, low=103.9, close=104., md=0., sb=0.)
        if scenario == 6 and b == 27:
            row.update(open=100., high=112., low=98., close=110., md=.30, sb=.10, rv=5., expansion=4.)
        row["middle"] = 100 + row["md"]
        if scenario >= 7 and 12 <= b <= 13:
            row.update(open=100. if b == 12 else 104., high=104.2 if b == 12 else 108.2,
                       low=99. if b == 12 else 104., close=104. if b == 12 else 108.,
                       md=0., sb=0., rv=5., expansion=4.,
                       middle=(99.9 if b == 12 else 99.8) if scenario == 9 else (100.2 if b == 12 else 100.4))
            if scenario == 8:
                row["pastWidth"] = 4.
        rows.append(row)
    return pd.DataFrame(rows, index=pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC"))


def test_native_full_state_path_freezes_band_waits_and_uses_next_bar_protection():
    r = replay(_state_fixture(), tick=.00001)
    assert r.iloc[12].quiet_count == 12
    assert r.iloc[13].pending_side == 1 and not r.iloc[13].burst
    assert r.iloc[14].pending_side == 1 and r.iloc[14].quiet_count == 0
    assert r.iloc[14].launch_band == .1  # current dynamic band would be .13
    signal = r.iloc[15]
    assert signal.burst and signal.route == "release_confirm" and signal.wait_bars == 2
    assert signal.quiet_bars == 12 and signal.trend_side == 1 and signal.pending_side == 0
    assert signal.peak_r == signal.current_r == 0 and np.isnan(signal.active_protection)
    assert signal.initial_stop == pytest.approx(98.74, abs=.000011)
    assert r.iloc[16].trail_armed and r.iloc[16].protection == pytest.approx(109.8, abs=.000011)
    assert r.iloc[16].active_protection < 104 < r.iloc[16].protection
    assert not r.iloc[16].exit and not r.iloc[16].burst
    assert r.iloc[17].protection == r.iloc[16].protection
    assert r.iloc[18].exit and r.iloc[18].trend_side == 0
    assert r.iloc[18].exit_price == 105  # adverse gap, not the higher protection
    assert r.iloc[18].peak_r == r.iloc[17].peak_r  # ignores stop-bar high130
    assert r.iloc[18].current_r == pytest.approx(1 / signal.risk)
    assert r.iloc[19].current_r == r.iloc[18].current_r
    assert r.burst.sum() == 1


def test_native_window_cancel_expiry_failed_gate_and_invalid_risk():
    cancelled = replay(_state_fixture(3), .00001)
    assert cancelled.iloc[14].pending_side == 0 and not cancelled.burst.any()
    expired = replay(_state_fixture(2), .00001)
    assert expired.iloc[15].pending_side == 1
    assert not expired.iloc[19].burst and expired.iloc[19].pending_side == 0
    assert not replay(_state_fixture(4), .00001).burst.any()
    invalid = replay(_state_fixture(5), .00001).iloc[15]
    assert invalid.burst and invalid.trend_side == 0
    assert np.isnan(invalid.risk) and np.isnan(invalid.current_r)


def test_native_price_first_consumes_prior_box_and_is_not_duplicated():
    r = replay(_state_fixture(7), .00001)
    assert r.iloc[13].burst and r.iloc[13].route == "price_first"
    assert r.iloc[13].launch_high == 100.4  # not the current bar's104.2
    assert r.iloc[13].quiet_count == r.iloc[13].pending_side == 0
    assert r.iloc[13].peak_r == 0 and np.isnan(r.iloc[13].active_protection)
    assert not r.iloc[14].burst and not r.iloc[15].burst
    assert r.iloc[14].risk == r.iloc[13].risk
    for scenario in (8, 9):
        negative = replay(_state_fixture(scenario), .00001)
        assert not negative.iloc[13].burst and negative.iloc[13].quiet_count == 13
        assert not negative.iloc[14].burst


def test_native_same_bar_exit_and_valid_new_launch_does_not_reenter():
    r = replay(_state_fixture(6), .00001)
    assert r.iloc[27].trend_side == 1 and r.iloc[27].quiet_count == 12
    assert r.iloc[28].exit and not r.iloc[28].burst
    assert r.iloc[28].trend_side == r.iloc[28].pending_side == 0


def test_frozen_default_never_emits_short_even_on_mirrored_path():
    f = _state_fixture()
    original_high = f.high.copy()
    f.high, f.low = 200 - f.low, 200 - original_high
    f.open, f.close = 200 - f.open, 200 - f.close
    f.md, f.sb, f.middle = -f.md, -f.sb, 200 - f.middle
    r = replay(f, .00001)
    assert not r.burst.any() and not r.burst_down.any()


def test_prefix_replay_and_future_mutation_leave_prior_features_and_state_unchanged():
    market = _ohlcv()
    full = features(market)
    assert_frame_equal(features(market.iloc[:371]), full.iloc[:371])
    f = _state_fixture(6)
    base = replay(f, .00001)
    assert_frame_equal(replay(f.iloc[:18], .00001), base.iloc[:18])
    f.iloc[18:, f.columns.get_indexer(["close", "high", "md", "rv", "atr"])] = 500.
    changed = replay(f, .00001)
    assert_frame_equal(changed.iloc[:18], base.iloc[:18])


@pytest.mark.parametrize("scale", [1e-3, 10000.])
def test_price_scale_with_tick_above_native_simple_zero_tolerance_preserves_signal_and_R(scale):
    f = _state_fixture()
    scaled = f.copy()
    columns = ["open", "high", "low", "close", "md", "sb", "middle", "atr",
               "ropeHigh", "ropeLow", "recentLow", "recentHigh"]
    scaled[columns] *= scale
    a, b = replay(f, .00001), replay(scaled, .00001 * scale)
    for column in ("burst", "exit", "route", "trend_side", "quiet_count", "pending_side", "wait_bars"):
        assert a[column].equals(b[column])
    # Floor/ceil can choose the adjacent outward tick at binary-representation
    # boundaries. The native Pine probe explicitly allows one price tick too.
    np.testing.assert_allclose(a.initial_stop, b.initial_stop / scale, atol=.000011, equal_nan=True)
    np.testing.assert_allclose(a.current_r, b.current_r, atol=1e-5, equal_nan=True)


@pytest.mark.parametrize("left,right,gt,ge,lt,le", [
    (0.49e-9, 0., True, True, False, False),
    (0.51e-9, 0., True, True, False, False),
    (-0.49e-9, 0., False, False, True, True),
    (-0.51e-9, 0., False, False, True, True),
    (100.0000000004, 100., True, True, False, False),
    (100.0000000006, 100., True, True, False, False),
    (0.0000010004, 0.000001, True, True, False, False),
    (3.9999999996, 4., False, False, True, True),
])
def test_native_series_comparisons_preserve_sub_nanounit_operands(left, right, gt, ge, lt, le):
    assert pine_gt(left, right) == gt
    assert pine_ge(left, right) == ge
    assert pine_lt(left, right) == lt
    assert pine_le(left, right) == le


def test_tiny_tick_has_arrow_but_native_simple_gate_invalidates_risk_reference():
    f = _state_fixture()
    columns = ["open", "high", "low", "close", "md", "sb", "middle", "atr",
               "ropeHigh", "ropeLow", "recentLow", "recentHigh"]
    f[columns] *= 1e-5
    r = replay(f, 1e-10)
    signal = r.iloc[15]
    assert signal.burst and not signal.risk_valid
    assert signal.trend_side == 0 and np.isnan(signal.risk)
    assert not r.exit.any()


@pytest.mark.parametrize("multiple,valid", [(1., False), (1.01, True), (1.1, True),
                                           (2., True), (4.9, True), (5.1, True)])
def test_native_simple_tick_zero_boundary_is_one_e_minus_ten_not_round9(multiple, valid):
    # qa/boundary_comparison.png: input x=1e-10, x>0=False/x==0=True;
    # 1.01x,1.1x,2x,4.9x,5.1x>0=True and 1.01x==0=False.
    ref = risk_reference(1, 100., 99., 1., tick=multiple * 1e-10)
    assert ref.valid == valid


def test_feature_comparison_precision_does_not_round_underlying_arithmetic():
    source = _ohlcv()
    source[["open", "high", "low", "close"]] *= 1e-10
    f = features(source)
    assert not f.flips.eq(0).all()  # series pair differences remain observable
    assert f.ready.iloc[340:].all()  # positive small series ATR stays positive
    assert f.atr.dropna().gt(0).all()  # arithmetic retains the actual small ATR
    assert f.e20.nunique() > 100  # arithmetic EMA was NOT rounded to9 places


def test_near_band_uses_native_series_precision_and_keeps_frozen_exact_band():
    frame = _state_fixture()
    frame.loc[frame.index[13], "md"] = .1000000004
    r = replay(frame.iloc[:14], .00001)
    assert r.iloc[-1].quiet_count == 0 and r.iloc[-1].pending_side == 1
    assert r.iloc[-1].launch_band == .1


def test_shadow_stop_and_activation_use_native_series_comparisons():
    stopped = path_reference(1, 100., 2., 99., 0., False,
                             100., 120., 99.0000000004, 110., 1., tick=.01)
    assert stopped.alive and np.isnan(stopped.exit_price)
    armed = path_reference(1, 100., 2., 95., 0., False,
                           100., 105., 99., 103.9999999996, 1., tick=.01)
    assert not armed.armed
    assert armed.current_r < 2.0


def test_native_series_probe_regression_does_not_copy_literal_rounding():
    # Native PEPE1H evidence: x=0.000000000489859236, x>0=True,
    # -x<0=True and close+x>close=True. The same run reports literal
    # 1e-10>0=False, so indiscriminate documentation-driven round9 is wrong.
    x = .000000000489859236
    assert pine_gt(x, 0.) and not pine_le(x, 0.) and pine_lt(-x, 0.)
    assert pine_gt(.000003 + x, .000003)
    assert np.floor(x * 1e9 + .5) / 1e9 == 0.  # rejected blanket round9


@pytest.mark.parametrize("tick", [None, 0, -1, np.nan, np.inf])
def test_no_missing_estimated_or_nonfinite_tick(tick):
    with pytest.raises(ValueError, match="tick"):
        replay(_state_fixture(), tick)


def test_rejects_invalid_index_and_price_geometry():
    source = _ohlcv()
    with pytest.raises(ValueError, match="DatetimeIndex"):
        features(source.reset_index(drop=True))
    with pytest.raises(ValueError, match="increasing"):
        features(source.iloc[::-1])
    invalid = source.copy()
    invalid.iloc[10, invalid.columns.get_loc("high")] = 1.
    with pytest.raises(ValueError, match="geometry"):
        features(invalid)
    invalid = source.copy()
    invalid.iloc[10, invalid.columns.get_loc("close")] = np.nan
    with pytest.raises(ValueError, match="finite"):
        features(invalid)


def test_empty_history_keeps_result_schema_and_timeframe_metadata():
    source = _ohlcv(0)
    source.attrs["minutes"] = 240
    f = features(source)
    result = replay(f, .01)
    assert result.empty
    assert {"burst", "route", "initial_stop", "active_protection", "exit"} <= set(result.columns)
    assert result.index.equals(source.index)
    assert result.attrs["minutes"] == 240


def test_missing_ready_cannot_be_truthy_nan_and_accidentally_skip_warmup():
    frame = _state_fixture()
    frame["ready"] = frame.ready.astype(object)
    frame.loc[frame.index[0], "ready"] = np.nan
    with pytest.raises(ValueError, match="ready"):
        replay(frame, .01)
