"""Synthetic parity, causality and unit invariance for offline altcoin inputs."""
from dataclasses import FrozenInstanceError

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal

from yoyo.data.altcoin_features import IMACDParams, MA_COLUMNS, build_altcoin_features
from yoyo.evaluation.imacd_startup_quality import build_features
from yoyo.monitor import signals


def bars_frame(n=540, freq="h", side=1):
    x = np.arange(n)
    close = 100 + 0.2 * np.sin(x / 8)
    bars = pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1,
         "close": close, "volume": 10.0 + (x % 19)},
        index=pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC"),
    )
    if n > 380:
        bars.iloc[380, :4] = [100, 141, 99, 140] if side == 1 else [100, 101, 59, 60]
    return bars


@pytest.mark.parametrize("freq", ["15min", "h", "4h"])
@pytest.mark.parametrize("side", [1, -1])
def test_default_matches_existing_monitor_and_focus_exactly(freq, side):
    bars = bars_frame(freq=freq, side=side)
    actual = build_altcoin_features(bars)
    legacy = build_features(bars)
    names = ["md", "sb", "sh", "atr", *MA_COLUMNS, "rope_high", "rope_low", "dense", "dense_recent",
             "prior_width_atr", "prior_crosses", "ready", "release_side", "near_zero_bars",
             "focus_start_i", "qualified", "focus_band", "release_band"]
    assert_frame_equal(actual[names], legacy[names], check_exact=True)
    raw = {name: bars[col].to_numpy() for name, col in zip("ohlcv", ("open", "high", "low", "close", "volume"))}
    raw["t"] = bars.index.asi8 // 1_000_000
    for name, expected in signals._compute(raw).items():
        np.testing.assert_equal(actual[name].to_numpy(), expected)
    assert actual.release_side.iloc[380] == side


@pytest.mark.parametrize("params", [IMACDParams(), IMACDParams(21, 5, 6, 0.05), IMACDParams(50, 15, 20, 0.2)])
def test_every_column_is_prefix_invariant_and_future_independent(params):
    bars = bars_frame(900)
    full = build_altcoin_features(bars, params)
    for end in (2, 35, 260, 352, 381, 540, 800):
        prefix = build_altcoin_features(bars.iloc[:end], params)
        assert_frame_equal(full.iloc[:end], prefix, check_exact=True)
    changed = bars.copy()
    changed.iloc[410:, :4] *= 7
    changed.iloc[410:, 4] *= 31
    assert_frame_equal(full.iloc[:410], build_altcoin_features(changed, params).iloc[:410], check_exact=True)


@pytest.mark.parametrize("side", [1, -1])
def test_price_unit_scaling_preserves_release_and_dimensionless_context(side):
    bars = bars_frame(side=side)
    scaled = bars.copy()
    scaled.iloc[:, :4] *= 10
    before, after = build_altcoin_features(bars), build_altcoin_features(scaled)
    for name in ("release_side", "near_zero_bars", "focus_start_i", "qualified", "ready"):
        assert_series_equal(before[name], after[name], check_exact=True)
    for name in ("relative_volume", "tr_expansion", "body_fraction", "close_location", "momentum3_atr",
                 "bb_width20", "atr_pct", "prior7d_atr_pct_mean", "prior7d_return"):
        np.testing.assert_allclose(before[name], after[name], rtol=1e-9, atol=1e-11, equal_nan=True)
    for name in ("md", "atr", "focus_band", "release_band", "release_zone_high", "release_zone_low"):
        np.testing.assert_allclose(before[name] * 10, after[name], rtol=1e-10, atol=1e-10, equal_nan=True)


@pytest.mark.parametrize("side", [1, -1])
def test_band_freezes_at_qualification_and_range_excludes_release(side):
    bars = bars_frame(400, side=side)
    bars.iloc[:, :4] = [100, 101, 99, 100]
    bars.iloc[340, :4] = [100, 110, 90, 100]  # Included before qualification.
    bars.iloc[352:380, :4] = [100, 100.1, 99.9, 100]  # Shrinks later ATR.
    bars.iloc[380, :4] = [100, 141, 99, 140] if side == 1 else [100, 101, 59, 60]
    actual = build_altcoin_features(bars)
    assert actual.qualified.iloc[351]
    assert actual.release_side.iloc[380] == side
    frozen = actual.atr.iloc[350] * 0.1
    assert actual.focus_band.iloc[351] == frozen
    assert actual.release_band.iloc[380] == frozen
    assert actual.atr.iloc[379] * 0.1 != frozen
    assert actual.near_zero_bars.iloc[380] == 40
    assert actual.focus_start_i.iloc[380] == 340
    assert actual.release_zone_high.iloc[380] == 110
    assert actual.release_zone_low.iloc[380] == 90
    assert actual.release_zone_high[actual.release_side.eq(0)].isna().all()
    giant = bars.copy()
    giant.iloc[380, :4] = [100, 2001, 1, 2000] if side == 1 else [100, 101, 0.5, 1]
    second = build_altcoin_features(giant)
    assert second.release_side.iloc[380] == side
    assert_frame_equal(actual.iloc[[380]][["release_band", "release_zone_high", "release_zone_low"]],
                       second.iloc[[380]][["release_band", "release_zone_high", "release_zone_low"]], check_exact=True)


def test_parameter_warmup_is_independent_and_has_no_global_effect():
    bars = bars_frame(700)
    baseline = build_features(bars)
    custom = build_altcoin_features(bars, IMACDParams(length_ma=50, length_signal=3, focus_min_bars=4))
    assert custom.ready.iloc[:500].eq(False).all()
    assert custom.ready.iloc[500]
    assert not np.allclose(custom.md, baseline.md, equal_nan=True)
    assert_frame_equal(build_features(bars), baseline, check_exact=True)
    with pytest.raises(FrozenInstanceError):
        IMACDParams().length_ma = 50


def test_context_windows_have_exact_availability_and_exclude_current_from_baselines():
    bars = bars_frame(520)
    out = build_altcoin_features(bars)
    i = 300
    assert out.relative_volume.iloc[i] == bars.volume.iloc[i] / bars.volume.iloc[i-20:i].median()
    assert out.prior7d_return.iloc[:169].isna().all()
    assert out.prior7d_return.iloc[169] == bars.close.iloc[168] / bars.close.iloc[0] - 1
    assert out.prior7d_atr_pct_mean.iloc[:181].isna().all()  # ATR seed at 13 + prior 168 bars.
    assert out.prior7d_atr_pct_mean.iloc[181] == pytest.approx(out.atr_pct.iloc[13:181].mean())
    assert out.bb_width_rank_prior240.iloc[:259].isna().all()
    assert out.bb_width_rank_prior240.iloc[i] == out.bb_width20.iloc[i-240:i].rank(pct=True).iloc[-1]
    changed = bars.copy()
    changed.iloc[i, :4] = [100, 2001, 1, 2000]
    changed.iloc[i, 4] *= 10
    altered = build_altcoin_features(changed)
    for name in ("bb_width_rank_prior240", "prior7d_atr_pct_mean", "prior7d_return"):
        assert out[name].iloc[i] == altered[name].iloc[i]
    assert altered.relative_volume.iloc[i] == 10 * out.relative_volume.iloc[i]


@pytest.mark.parametrize("freq,seven_days", [("15min", 672), ("h", 168), ("4h", 42)])
def test_seven_day_return_is_calendar_time_not_a_shared_bar_count(freq, seven_days):
    bars = bars_frame(800, freq=freq)
    out = build_altcoin_features(bars)
    i = seven_days + 10
    assert out.prior7d_return.iloc[i] == bars.close.iloc[i-1] / bars.close.iloc[i-seven_days-1] - 1


def test_zero_denominators_remain_missing_and_no_infinities_are_emitted():
    bars = bars_frame(400)
    bars.iloc[:, :] = [100, 100, 100, 100, 0]
    out = build_altcoin_features(bars)
    assert out[["relative_volume", "tr_expansion", "body_fraction", "close_location", "momentum3_atr"]].isna().all().all()
    assert not np.isinf(out.select_dtypes(include="number").astype(float)).any().any()


@pytest.mark.parametrize("bad", ["empty", "one", "naive", "other_tz", "duplicate", "gap", "reverse", "unaligned",
                                 "missing_column", "duplicate_column", "nan", "negative_volume", "bad_range", "unconfirmed", "irregular_week"])
def test_invalid_inputs_fail_closed(bad):
    bars = bars_frame(40)
    if bad == "empty": bars = bars.iloc[:0]
    elif bad == "one": bars = bars.iloc[:1]
    elif bad == "naive": bars.index = bars.index.tz_localize(None)
    elif bad == "other_tz": bars.index = bars.index.tz_convert("Asia/Shanghai")
    elif bad == "duplicate": bars = pd.concat([bars, bars.iloc[[-1]]])
    elif bad == "gap": bars = bars.drop(bars.index[20])
    elif bad == "reverse": bars = bars.iloc[::-1]
    elif bad == "unaligned": bars.index += pd.Timedelta(minutes=1)
    elif bad == "missing_column": bars = bars.drop(columns="volume")
    elif bad == "duplicate_column": bars = pd.concat([bars, bars[["volume"]]], axis=1)
    elif bad == "nan": bars.iloc[2, 0] = np.nan
    elif bad == "negative_volume": bars.iloc[2, 4] = -1
    elif bad == "bad_range": bars.iloc[2, 1] = 0
    elif bad == "unconfirmed": bars["confirm"] = "0"
    elif bad == "irregular_week": bars.index = pd.date_range("1970-01-01", periods=40, freq="5h", tz="UTC")
    with pytest.raises(ValueError):
        build_altcoin_features(bars)


@pytest.mark.parametrize("kwargs", [{"length_ma": 1}, {"length_ma": True}, {"length_signal": 2.5},
                                    {"focus_min_bars": 1}, {"focus_atr_band": -0.01},
                                    {"focus_atr_band": np.nan}, {"focus_atr_band": True}])
def test_invalid_parameters_fail_at_construction(kwargs):
    with pytest.raises(ValueError):
        IMACDParams(**kwargs)
