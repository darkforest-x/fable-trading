"""Synthetic parity and causality checks for research-only startup features."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.evaluation.imacd_startup_quality import MA_COLUMNS, build_features
from yoyo.monitor import signals


def candles_frame(n: int = 400, freq: str = "h", oscillate: bool = False) -> pd.DataFrame:
    close = 100 + (.2 * np.sin(np.arange(n) / 8) if oscillate else np.zeros(n))
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1,
         "close": close, "volume": np.full(n, 10.0)},
        index=pd.date_range("1970-01-01", periods=n, freq=freq, tz="UTC"),
    )


def monitor_candles(bars: pd.DataFrame) -> list[dict]:
    return [dict(t=int(t.value // 1_000_000), o=row.open, h=row.high,
                 l=row.low, c=row.close, v=row.volume)
            for t, row in bars.iterrows()]


def assert_monitor_parity(bars: pd.DataFrame, timeframe: str = "1H") -> pd.DataFrame:
    actual = build_features(bars)
    result = signals.analyze(monitor_candles(bars), [], timeframe)
    events = {e["bar_open_ms"]: e for e in result["events"] if e["kind"] == "tv_start"}
    start_lookup = {int(t.value // 1_000_000): i for i, t in enumerate(bars.index)}
    for i, row in enumerate(result["chart"]):
        point = actual.iloc[i]
        event = events.get(row["t"])
        side = {"long": 1, "short": -1}.get(row["tv_start_side"], 0)
        assert point.release_side == side
        assert point.qualified == row["focus"]
        assert point.ready == row["ready"]
        expected_band = np.nan if row["focus_band"] is None else row["focus_band"]
        np.testing.assert_equal(point.focus_band, expected_band)
        near = event["near_zero_bars"] if event else row["near_zero_bars"]
        assert point.near_zero_bars == near
        start = event["focus_start_ms"] if event else row["focus_start_ms"]
        if start is None:
            assert pd.isna(point.focus_start_i)
        else:
            assert point.focus_start_i == start_lookup[start]
        if event:
            assert point.release_band == event["focus_band"]
        else:
            assert np.isnan(point.release_band)
        for name in ("md", "sb", "atr", *MA_COLUMNS, "dense"):
            expected = np.nan if row[name] is None else row[name]
            np.testing.assert_equal(point[name], expected)
    assert np.count_nonzero(actual.release_side) == len(events)
    return actual


def patch_features(monkeypatch, changes: dict[str, dict[int, float]]) -> None:
    original = signals._compute

    def compute(b):
        f = original(b)
        for key, values in changes.items():
            for i, value in values.items():
                if i < len(b["t"]):
                    f[key][i] = value
        return f

    monkeypatch.setattr(signals, "_compute", compute)


@pytest.mark.parametrize("timeframe,freq", [("15m", "15min"), ("1H", "h"), ("4H", "4h")])
@pytest.mark.parametrize("side", [1, -1])
def test_real_release_parity_in_both_directions_and_all_monitor_timeframes(timeframe, freq, side):
    bars = candles_frame(420, freq=freq)
    bars.loc[bars.index[380], ["open", "high", "low", "close"]] = (
        [100, 141, 99, 140] if side == 1 else [100, 101, 59, 60]
    )
    out = assert_monitor_parity(bars, timeframe)
    assert out.iloc[380].release_side == side
    assert out.iloc[380].near_zero_bars == 40
    assert out.iloc[380].focus_start_i == 340
    assert not out.qualified.iloc[:351].any()
    assert out.qualified.iloc[351]
    assert not out.qualified.iloc[380]


def test_frozen_band_equality_and_signal_line_only_departure_match_monitor(monkeypatch):
    patch_features(monkeypatch, {
        "atr": {351: .01, 352: 100.0},
        "md": {352: .2, 353: .200001, 366: -.200001, 379: .1},
        "sb": {352: .2, 353: .1, 366: -.1, 379: .3},
    })
    out = assert_monitor_parity(candles_frame(382))
    assert out.qualified.iloc[352]  # Inclusive equality despite shrunken ATR.
    assert out.near_zero_bars.iloc[352] == 13
    assert out.release_band.iloc[353] == .2  # Ignores enlarged candidate band.
    assert out.release_side.iloc[353] == 1
    assert out.release_side.iloc[366] == -1
    assert out.release_side.iloc[379] == 0  # Signal line alone cannot choose direction.
    assert out.near_zero_bars.iloc[379] == 0
    assert out.near_zero_bars.iloc[380] == 1


def test_future_perturbation_and_shorter_history_leave_every_prefix_column_unchanged():
    original = candles_frame(520, oscillate=True)
    original.loc[original.index[380], ["open", "high", "low", "close"]] = [100, 141, 99, 140]
    changed = original.copy(deep=True)
    changed.loc[changed.index[410]:, ["open", "high", "low", "close", "volume"]] = [800, 1001, 1, 1000, 9000]
    out = build_features(original)
    assert_frame_equal(out.iloc[:410], build_features(changed).iloc[:410], check_exact=True)
    assert_frame_equal(out.iloc[:410], build_features(original.iloc[:410]), check_exact=True)


def test_release_candle_size_cannot_rewrite_prior_formation():
    ordinary = candles_frame(390, oscillate=True)
    ordinary.loc[ordinary.index[380], ["open", "high", "low", "close"]] = [100, 141, 99, 140]
    giant = ordinary.copy(deep=True)
    giant.loc[giant.index[380], ["open", "high", "low", "close"]] = [100, 2001, 1, 2000]
    a, b = build_features(ordinary), build_features(giant)
    assert a.release_side.iloc[380] == b.release_side.iloc[380] == 1
    columns = ["formation_early_width", "formation_late_width", "contraction_ratio",
               "proximity_atr", "focus_start_i", "near_zero_bars", "release_band",
               "keep_contraction", "keep_proximity"]
    assert_frame_equal(a.iloc[[380]][columns], b.iloc[[380]][columns], check_exact=True)
    assert a.separation_delta.iloc[380] != b.separation_delta.iloc[380]


def test_feature_windows_use_raw_widths_and_one_prior_atr_denominator(monkeypatch):
    # Inject varying ATR after qualification to distinguish a median of ratios
    # from the specified median distance divided by one ATR[i-1].
    patch_features(monkeypatch, {"atr": {i: (i - 345) * .1 for i in range(352, 380)}})
    bars = candles_frame(390, oscillate=True)
    bars.loc[bars.index[380], ["open", "high", "low", "close"]] = [100, 141, 99, 140]
    out = build_features(bars)
    i, start = 380, int(out.focus_start_i.iloc[380])
    assert out.release_side.iloc[i] == 1
    width = out.rope_high - out.rope_low
    early = width.iloc[start:start + 6].median()
    late = width.iloc[i - 6:i].median()
    expected_outside = np.maximum.reduce([
        out.rope_low.iloc[i - 12:i].to_numpy() - bars.close.iloc[i - 12:i].to_numpy(),
        bars.close.iloc[i - 12:i].to_numpy() - out.rope_high.iloc[i - 12:i].to_numpy(),
        np.zeros(12),
    ])
    prior_atr = out.atr.iloc[i - 1]
    fast = (out.sma20 + out.ema20) / 2
    slow = out[["sma60", "ema60", "sma120", "ema120"]].mean(axis=1)
    expected_delta = ((fast - slow).iloc[i] - (fast - slow).iloc[i - 3]) / prior_atr
    assert out.formation_early_width.iloc[i] == early
    assert out.formation_late_width.iloc[i] == late
    assert out.contraction_ratio.iloc[i] == pytest.approx(late / early)
    assert out.proximity_atr.iloc[i] == np.median(expected_outside) / prior_atr
    assert out.separation_delta.iloc[i] == expected_delta
    assert np.isnan(out.separation_delta.iloc[i - 1])


def test_zero_width_ratio_is_defined_without_infinity():
    bars = candles_frame(381)
    bars.loc[bars.index[380], ["open", "high", "low", "close"]] = [100, 141, 99, 140]
    out = build_features(bars)
    assert out.formation_early_width.iloc[380] == 0
    assert out.formation_late_width.iloc[380] == 0
    assert out.contraction_ratio.iloc[380] == 1
    assert out.keep_contraction.iloc[380]
    assert out.keep_proximity.iloc[380]
    assert out.keep_separation.iloc[380]
    assert not np.isinf(out.select_dtypes(include="number").to_numpy(dtype=float, na_value=np.nan)).any()


def test_positive_late_width_divided_by_zero_early_width_is_unknown(monkeypatch):
    changes = {"rope_high": {i: 101.0 for i in range(374, 380)}}
    patch_features(monkeypatch, changes)
    bars = candles_frame(381)
    bars.loc[bars.index[380], ["open", "high", "low", "close"]] = [100, 141, 99, 140]
    out = build_features(bars)
    assert out.formation_early_width.iloc[380] == 0
    assert out.formation_late_width.iloc[380] == 1
    assert np.isnan(out.contraction_ratio.iloc[380])
    assert not out.keep_contraction.iloc[380]


def test_empty_short_and_zero_range_inputs_remain_unqualified():
    empty = candles_frame(0)
    out = build_features(empty)
    assert out.empty
    assert out.index.equals(empty.index)
    assert str(out.focus_start_i.dtype) == "Int64"
    assert not build_features(candles_frame(340)).qualified.any()
    flat = candles_frame(400)
    flat["high"] = flat["low"] = flat["close"]
    out = build_features(flat)
    assert not out.ready.any()
    assert not out.release_side.any()
    assert not out.keep_proximity.any()


@pytest.mark.parametrize("defect", ["gap", "duplicate", "reverse", "nat", "naive", "nonutc", "nan",
                                    "inf", "missing", "duplicate_column", "body", "negative_volume"])
def test_invalid_inputs_are_rejected(defect):
    bars = candles_frame(400)
    if defect == "gap":
        bars = bars.drop(bars.index[100])
    elif defect == "duplicate":
        idx = list(bars.index)
        idx[100] = idx[99]
        bars.index = pd.DatetimeIndex(idx)
    elif defect == "reverse":
        bars = bars.iloc[::-1]
    elif defect == "nat":
        idx = list(bars.index)
        idx[100] = pd.NaT
        bars.index = pd.DatetimeIndex(idx)
    elif defect == "naive":
        bars.index = bars.index.tz_localize(None)
    elif defect == "nonutc":
        bars.index = bars.index.tz_convert("Asia/Shanghai")
    elif defect in ("nan", "inf"):
        bars.loc[bars.index[100], "close"] = np.nan if defect == "nan" else np.inf
    elif defect == "missing":
        bars = bars.drop(columns="volume")
    elif defect == "duplicate_column":
        bars = pd.concat([bars, bars[["volume"]]], axis=1)
    elif defect == "body":
        bars.loc[bars.index[100], "close"] = 110
    else:
        bars.loc[bars.index[100], "volume"] = -1
    with pytest.raises(ValueError):
        build_features(bars)


def test_build_features_does_not_modify_input():
    bars = candles_frame(381, oscillate=True)
    original = bars.copy(deep=True)
    build_features(bars)
    assert_frame_equal(bars, original, check_exact=True)
