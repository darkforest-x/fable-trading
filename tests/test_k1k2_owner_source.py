"""Deterministic property/oracle tests, without Hypothesis or market data.

Source formulas are independently expressed below. Seeded valid OHLC generators
exercise prefix/mirror/scale properties; hand-built geometry provides explicit
non-vacuous witnesses for gates, ranking, and the last-completed-bar boundary.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.data.k1k2_owner_source import (
    BAR_COLUMNS, CANDIDATE_COLUMNS, FEATURE_COLUMNS, MA_COLUMNS,
    add_features, detect_candidates,
)


def flat_bars(n=130):
    return pd.DataFrame({
        "open_time": pd.date_range("2023-01-01", periods=n, freq="h", tz="UTC"),
        "open": np.full(n, 100.), "high": np.full(n, 101.),
        "low": np.full(n, 99.), "close": np.full(n, 100.),
        "volume": np.full(n, 10.),
    })


def real_witness():
    bars = flat_bars(122)
    bars.loc[119, ["open", "high", "low", "close"]] = [99., 103., 98.8, 102.8]
    bars.loc[120, ["open", "high", "low", "close"]] = [102., 103., 101., 102.5]
    bars.loc[121, ["open", "high", "low", "close"]] = [100.9, 101.5, 99.6, 101.2]
    return bars


def shape_fixture(n=12):
    """Known feature values isolate shape gates from recursive feature changes."""
    bars = flat_bars(n)
    bars[["open", "high", "low", "close"]] = [101., 102., 100., 101.5]
    featured = add_features(bars)
    featured["sma40_hl2"] = 100.
    featured["atr14"] = 2.
    featured[MA_COLUMNS] = 100.
    featured[["rope_high", "rope_low", "rope_mid"]] = 100.
    featured["hl2"] = (featured.high + featured.low) / 2
    featured["ma_candle_side"] = 1
    featured.loc[n - 3, ["open", "high", "low", "close"]] = [100., 102.1, 99.9, 102.]
    featured.loc[n - 1, ["open", "high", "low", "close"]] = [101., 101.5, 99.5, 101.]
    return featured


def scalar_features(bars):
    """Slow scalar Pine-state oracle: no pandas rolling/ewm or module helpers."""
    records = []
    closes, hl2s, trs, emas = [], [], [], {}
    atr = None
    previous = None
    last_segment = object()
    for row in bars.to_dict("records"):
        segment = row.get("segment_id", 0)
        if previous is None or row["open_time"] - previous["open_time"] != pd.Timedelta(hours=1) or segment != last_segment:
            closes, hl2s, trs, emas = [], [], [], {}
            atr = None
            previous = None
        h, l, c = row["high"], row["low"], row["close"]
        hl2 = (h + l) / 2
        tr = h - l if previous is None else max(h - l, abs(h - previous["close"]), abs(l - previous["close"]))
        closes.append(c)
        hl2s.append(hl2)
        trs.append(tr)
        if len(trs) == 14:
            atr = sum(trs) / 14
        elif len(trs) > 14:
            atr = (atr * 13 + tr) / 14
        result = {"hl2": hl2, "true_range": tr, "atr14": math.nan if atr is None else atr,
                  "sma40_hl2": sum(hl2s[-40:]) / 40 if len(hl2s) >= 40 else math.nan}
        for period in (20, 60, 120):
            alpha = 2 / (period + 1)
            emas[period] = c if period not in emas else alpha * c + (1 - alpha) * emas[period]
            result["ema%d" % period] = emas[period]
            result["sma%d" % period] = sum(closes[-period:]) / period if len(closes) >= period else math.nan
        rope = [result[name] for name in MA_COLUMNS]
        complete = all(math.isfinite(value) for value in rope)
        result.update(rope_high=max(rope) if complete else math.nan,
                      rope_low=min(rope) if complete else math.nan,
                      rope_mid=sum(rope) / 6 if complete else math.nan,
                      ma_candle_side=0 if len(hl2s) < 40 else (1 if hl2 >= result["sma40_hl2"] else -1))
        records.append(result)
        previous, last_segment = row, segment
    return pd.DataFrame(records, columns=FEATURE_COLUMNS)


def scalar_candidates(featured):
    """Enumerate every pair and reduce by source quality; no production helpers."""
    records = featured.to_dict("records")
    winners = {}
    for i, one in enumerate(records):
        values = [one[x] for x in MA_COLUMNS + ["rope_high", "rope_low", "rope_mid", "atr14", "sma40_hl2"]]
        if not all(math.isfinite(x) for x in values) or one["atr14"] <= 0:
            continue
        for j in range(i + 2, min(i + 9, len(records))):
            two = records[j]
            if one["segment_id"] != two["segment_id"]:
                continue
            atr1, atr2 = one["atr14"], two["atr14"]
            ma1, ma2 = one["sma40_hl2"], two["sma40_hl2"]
            r1, r2 = one["high"] - one["low"], two["high"] - two["low"]
            if r1 <= 0 or r2 <= 0 or not math.isfinite(atr2) or atr2 <= 0 or not math.isfinite(ma2):
                continue
            for d in (1, -1):
                ratio1, ratio2 = abs(one["close"] - one["open"]) / r1, abs(two["close"] - two["open"]) / r2
                loc1 = (one["close"] - one["low"]) / r1 if d == 1 else (one["high"] - one["close"]) / r1
                wick = (min(two["open"], two["close"]) - two["low"]) / r2 if d == 1 else (two["high"] - max(two["open"], two["close"])) / r2
                reject = (two["close"] - two["low"]) / r2 if d == 1 else (two["high"] - two["close"]) / r2
                touch = (ma2 - two["low"]) / atr2 if d == 1 else (two["high"] - ma2) / atr2
                body_side = min(two["open"], two["close"]) >= ma2 if d == 1 else max(two["open"], two["close"]) <= ma2
                conditions = [d * (one["close"] - one["open"]) > 0, ratio1 >= .65,
                              r1 / atr1 >= .95, loc1 >= .70,
                              d * (ma1 - one["open"]) / atr1 >= -.05,
                              d * (one["close"] - ma1) / atr1 >= -.05,
                              (1 if (one["high"] + one["low"]) / 2 >= ma1 else -1) == d,
                              wick >= .25, ratio2 <= .5, reject >= .25,
                              0 <= touch <= 1.5, d * (two["close"] - ma2) >= 0, body_side]
                for middle in records[i + 1:j]:
                    m = middle["sma40_hl2"]
                    conditions += [math.isfinite(m), d * (middle["close"] - m) >= 0,
                                   (1 if (middle["high"] + middle["low"]) / 2 >= m else -1) == d]
                if not all(conditions):
                    continue
                lo, hi, mid = one["rope_low"], one["rope_high"], one["rope_mid"]
                lower, upper = sorted([one["open"], one["close"]])
                cover = min(1., max(0., min(upper, hi) - max(lower, lo)) / (hi - lo)) if hi > lo else float(lower <= mid <= upper)
                enter = (lo - one["open"]) / atr1 if d == 1 else (one["open"] - hi) / atr1
                leave = (one["close"] - hi) / atr1 if d == 1 else (lo - one["close"]) / atr1
                clamp = lambda value: max(0., min(1., value))
                quality = (min(1., cover) + clamp(ratio1) + clamp(r1 / atr1 / 2)
                           + clamp((min(enter, leave) + .15) / .5)) / 4
                key = (two["open_time"], d)
                candidate = (quality, -(j - i), one["open_time"])
                if key not in winners or candidate[:2] > winners[key][:2]:
                    winners[key] = candidate
    return winners


def generated_bars(seed, n=400):
    rng = np.random.default_rng(seed)
    bars = flat_bars(n)
    bars["open"] = 100 + np.cumsum(rng.normal(0, .8, n))
    bars["close"] = bars.open + rng.normal(0, 1.1, n)
    bars["high"] = np.maximum(bars.open, bars.close) + rng.uniform(.05, 1.4, n)
    bars["low"] = np.minimum(bars.open, bars.close) - rng.uniform(.05, 1.4, n)
    bars["volume"] = rng.integers(0, 1000, n).astype(float)
    return bars


@pytest.mark.parametrize("seed", range(6))
def test_features_match_independent_scalar_pine_oracle(seed):
    bars = generated_bars(seed)
    bars.loc[210:, "open_time"] += pd.Timedelta(hours=2)
    actual = add_features(bars)
    oracle = scalar_features(bars)
    np.testing.assert_allclose(actual[FEATURE_COLUMNS], oracle, rtol=2e-13, atol=2e-13, equal_nan=True)
    assert actual.loc[209, "segment_id"] != actual.loc[210, "segment_id"]
    assert actual.loc[210:328, "rope_mid"].isna().all()
    assert math.isfinite(actual.loc[329, "rope_mid"])


def test_last_closed_bar_is_candidate_without_next_open_and_warmup_is_six_mas():
    bars = real_witness()
    featured = add_features(bars)
    assert featured.loc[:118, ["rope_high", "rope_low", "rope_mid"]].isna().all().all()
    assert featured.loc[:118, "ema120"].notna().all()
    candidates = detect_candidates(featured)
    assert len(candidates) == 1
    row = candidates.iloc[0]
    assert row.k1_time == bars.iloc[119].open_time
    assert row.k2_time == bars.iloc[-1].open_time
    assert row.k1_decision_time == row.k1_time + pd.Timedelta(hours=1)
    assert row.decision_time == row.k2_time + pd.Timedelta(hours=1)
    assert row.initial_stop == bars.iloc[-1].low
    assert row.gap_bars == 2
    assert row.middle_wrong_closes == 0
    assert row.middle_aligned_ma_bars == 1
    assert not {"entry_price", "net_return", "fee_to_risk", "target"}.intersection(candidates.columns)


@pytest.mark.parametrize("seed", range(5))
def test_pair_enumeration_oracle_and_future_prefix_properties(seed):
    # The witness guarantees non-vacuous prefix comparisons regardless of RNG.
    bars = pd.concat([real_witness(), generated_bars(seed, 320)], ignore_index=True)
    bars["open_time"] = pd.date_range("2023-01-01", periods=len(bars), freq="h", tz="UTC")
    original = bars.copy(deep=True)
    featured = add_features(bars)
    untouched_features = featured.copy(deep=True)
    candidates = detect_candidates(featured)
    expected = scalar_candidates(featured)
    assert len(candidates) == len(expected) > 0
    for row in candidates.itertuples():
        quality, negative_gap, k1_time = expected[(row.k2_time, row.direction)]
        assert row.k1_quality == pytest.approx(quality, abs=1e-14)
        assert row.gap_bars == -negative_gap
        assert row.k1_time == k1_time
    for end in (122, 160, 300):
        prefix = add_features(bars.iloc[:end])
        assert_frame_equal(featured.iloc[:end].reset_index(drop=True), prefix)
        prefix_candidates = detect_candidates(prefix)
        assert_frame_equal(candidates.loc[candidates.k2_time <= bars.iloc[end - 1].open_time].reset_index(drop=True), prefix_candidates)
        changed = bars.copy(deep=True)
        changed.loc[end:, ["open", "high", "low", "close"]] *= 1.5
        changed.loc[end:, "volume"] *= 3
        changed_features = add_features(changed)
        assert_frame_equal(featured.iloc[:end], changed_features.iloc[:end])
        changed_candidates = detect_candidates(changed_features)
        assert_frame_equal(prefix_candidates, changed_candidates.loc[changed_candidates.k2_time <= bars.iloc[end - 1].open_time].reset_index(drop=True))
    assert_frame_equal(bars, original)
    assert_frame_equal(featured, untouched_features)


def test_short_gap_wins_exact_quality_tie_then_higher_quality_wins():
    featured = shape_fixture()
    featured.loc[7, ["open", "high", "low", "close"]] = featured.loc[9, ["open", "high", "low", "close"]].to_numpy()
    result = detect_candidates(featured)
    last = result.loc[result.k2_time == featured.iloc[-1].open_time].iloc[0]
    assert last.gap_bars == 2
    featured.loc[7, ["open", "high", "low", "close"]] = [99.8, 102.2, 99.7, 102.]
    result = detect_candidates(featured)
    last = result.loc[result.k2_time == featured.iloc[-1].open_time].iloc[0]
    assert last.gap_bars == 4


@pytest.mark.parametrize("gap", [1, 2, 3, 8, 9])
def test_gap_interval_is_inclusive_two_to_eight(gap):
    featured = shape_fixture(14)
    base = [101., 102., 100., 101.5]
    featured.loc[11, ["open", "high", "low", "close"]] = base
    featured.loc[13 - gap, ["open", "high", "low", "close"]] = [100., 102.1, 99.9, 102.]
    result = detect_candidates(featured)
    last = result.loc[result.k2_time == featured.iloc[-1].open_time]
    assert len(last) == int(2 <= gap <= 8)


@pytest.mark.parametrize("index,column,value", [
    (9, "open", 101.),                  # K1 body too small and fails entry depth
    (9, "atr14", 3.),                  # K1 range/ATR below .95
    (9, "sma40_hl2", 99.8),            # K1 body starts too far above MA
    (9, "sma40_hl2", 102.2),           # K1 exit below MA, HL2 wrong side
    (9, "sma120", np.nan),             # no partial six-MA rope
    (9, "rope_mid", np.nan),
    (10, "close", 99.9),               # wrong middle close (geometry adjusted below)
    (10, "low", 97.),                  # close still aligned but HL2 below MA
    (10, "sma40_hl2", np.nan),          # unknown middle is not neutral
    (11, "low", 100.1),                # no physical touch
    (11, "low", 96.9),                 # touch depth exceeds 1.5 ATR
    (11, "open", 99.8),                # MA enters K2 body
    (11, "high", 107.),                # rejection wick share too small
    (11, "atr14", 0.),                 # undefined normalized geometry
])
def test_each_shape_failure_rejects_last_candidate(index, column, value):
    featured = shape_fixture()
    assert len(detect_candidates(featured)) == 1
    featured.loc[index, column] = value
    if column == "close" and value < featured.loc[index, "low"]:
        featured.loc[index, "low"] = value - .1
    result = detect_candidates(featured)
    assert not result.k2_time.eq(featured.iloc[-1].open_time).any()


def test_exact_touch_boundary_body_boundary_and_k2_native_colour_are_not_extra_gates():
    featured = shape_fixture()
    featured.loc[11, ["open", "high", "low", "close"]] = [101., 102., 100., 100.5]
    result = detect_candidates(featured)
    assert len(result) == 1  # Bearish native candle can reject a long setup.
    assert result.iloc[0].k2_touch_depth == 0
    featured.loc[11, ["open", "high", "low", "close"]] = [100., 100.5, 99., 100.]
    result = detect_candidates(featured)
    assert len(result) == 1  # The MA can meet, but cannot enter, the doji body.


@pytest.mark.parametrize("gate,ohlc,atr,ma,column,rejected", [
    ("body", [101., 120., 100., 114.], 20., 101., "open", 101.01),
    ("location", [100., 120., 100., 114.], 20., 100., "close", 113.99),
    ("range_atr", [100., 119., 100., 119.], 20., 100., "atr14", 20.001),
    ("entry_depth", [101., 120., 100., 117.], 20., 100., "open", 101.001),
])
def test_k1_approved_thresholds_include_exact_boundary(gate, ohlc, atr, ma, column, rejected):
    featured = shape_fixture()
    featured.loc[9, ["open", "high", "low", "close"]] = ohlc
    featured.loc[9, ["atr14", "sma40_hl2"]] = [atr, ma]
    assert len(detect_candidates(featured)) == 1, gate
    featured.loc[9, column] = rejected
    assert detect_candidates(featured).empty, gate


@pytest.mark.parametrize("ohlc,column,rejected", [
    ([101., 107., 99., 101.], "high", 107.001),  # Wick / range = .25.
    ([101., 105., 99., 104.], "close", 104.001), # Body / range = .50.
    ([101., 101.5, 97., 101.], "low", 96.999),  # Touch / ATR = 1.50.
])
def test_k2_approved_thresholds_include_exact_boundary(ohlc, column, rejected):
    featured = shape_fixture()
    featured.loc[11, ["open", "high", "low", "close"]] = ohlc
    assert len(detect_candidates(featured)) == 1
    featured.loc[11, column] = rejected
    assert detect_candidates(featured).empty


def test_source_ma_side_equality_is_long_and_not_symmetric_at_equality():
    featured = shape_fixture()
    featured.loc[10, ["open", "high", "low", "close"]] = [100., 101., 99., 100.]
    assert len(detect_candidates(featured)) == 1
    mirrored = mirror_featured(featured)
    assert detect_candidates(mirrored).empty  # HL2 == MA maps to +1 in Pine.


def mirror_featured(featured):
    result = featured.copy(deep=True)
    for column in ("open", "close", "hl2", "sma40_hl2", "rope_mid") + tuple(MA_COLUMNS):
        result[column] = 200 - featured[column]
    result["high"], result["low"] = 200 - featured.low, 200 - featured.high
    result["rope_high"], result["rope_low"] = 200 - featured.rope_low, 200 - featured.rope_high
    result["ma_candle_side"] = -featured.ma_candle_side
    return result


def test_direction_mirror_has_equal_geometry_away_from_equal_ma_side():
    featured = shape_fixture()
    long = detect_candidates(featured).iloc[0]
    short = detect_candidates(mirror_featured(featured)).iloc[0]
    assert long.direction == 1 and short.direction == -1
    assert long.gap_bars == short.gap_bars
    for column in ("k1_quality", "range_atr", "body_ratio", "k2_wick_share", "k2_touch_depth", "k1_entry_depth", "k1_exit_depth"):
        assert long[column] == pytest.approx(short[column], abs=1e-13)
    assert short.initial_stop == pytest.approx(200 - long.initial_stop)


def test_scale_invariance_and_event_ids_do_not_depend_on_index_or_prices():
    bars = real_witness()
    first = detect_candidates(add_features(bars), venue="binance_usdm", symbol="BTCUSDT")
    scaled = bars.copy()
    scaled[["open", "high", "low", "close"]] *= 7
    scaled.index = np.arange(3000, 3000 + len(bars))
    second = detect_candidates(add_features(scaled), venue="binance_usdm", symbol="BTCUSDT")
    assert first.event_id.tolist() == second.event_id.tolist()
    for column in ("k1_quality", "range_atr", "body_ratio", "k2_wick_share", "k2_touch_depth"):
        np.testing.assert_allclose(first[column], second[column], rtol=1e-12, atol=1e-12)
    assert detect_candidates(add_features(bars), venue="OKX", symbol="BTCUSDT").iloc[0].event_id != first.iloc[0].event_id
    assert detect_candidates(add_features(bars), venue="binance_usdm", symbol="ETHUSDT").iloc[0].event_id != first.iloc[0].event_id


def test_candidates_are_not_deduplicated_by_k1_and_have_unique_stable_ids():
    featured = shape_fixture(14)
    featured.loc[11, ["open", "high", "low", "close"]] = [101., 101.5, 99.5, 101.]
    featured.loc[9, ["open", "high", "low", "close"]] = [100., 102.1, 99.9, 102.]
    result = detect_candidates(featured)
    # The intervening bar 12 also touches exactly and is a valid K2.
    assert len(result) == 3
    assert result.k1_time.nunique() == 1
    assert result.event_id.nunique() == 3
    assert result.k2_time.is_monotonic_increasing


def test_segment_reset_and_no_pair_crosses_gap_or_explicit_reset():
    featured = shape_fixture()
    featured.loc[10:, "open_time"] += pd.Timedelta(hours=1)
    featured = featured.drop(columns="segment_id")
    assert detect_candidates(featured).empty
    bars = pd.concat([real_witness(), real_witness()], ignore_index=True)
    bars["open_time"] = pd.date_range("2023-01-01", periods=len(bars), freq="h", tz="UTC")
    bars["segment_id"] = [7] * 122 + [9] * 122
    featured = add_features(bars)
    assert_frame_equal(featured.loc[122:, FEATURE_COLUMNS].reset_index(drop=True), featured.loc[:121, FEATURE_COLUMNS])
    assert detect_candidates(featured).segment_id.tolist() == [7, 9]


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_timezone_resolution_and_exact_hour_grid(unit):
    bars = real_witness()
    expected = add_features(bars)
    bars.open_time = bars.open_time.dt.tz_convert("Asia/Shanghai").dt.as_unit(unit)
    assert_frame_equal(expected, add_features(bars))


@pytest.mark.parametrize("fault", ["naive", "numeric", "missing_time", "off_grid", "duplicate", "reverse", "nan_price", "inf_volume", "negative_volume", "bool_price", "bad_geometry", "gap_same_segment", "segment_reappears", "bool_segment"])
def test_invalid_inputs_fail_closed(fault):
    bars = flat_bars()
    if fault == "naive":
        bars.open_time = bars.open_time.dt.tz_localize(None)
    elif fault == "numeric":
        bars.open_time = np.arange(len(bars))
    elif fault == "missing_time":
        bars.loc[5, "open_time"] = pd.NaT
    elif fault == "off_grid":
        bars.loc[5, "open_time"] += pd.Timedelta(minutes=1)
    elif fault == "duplicate":
        bars.loc[5, "open_time"] = bars.loc[4, "open_time"]
    elif fault == "reverse":
        bars = bars.iloc[::-1]
    elif fault == "nan_price":
        bars.loc[5, "open"] = np.nan
    elif fault == "inf_volume":
        bars.loc[5, "volume"] = np.inf
    elif fault == "negative_volume":
        bars.loc[5, "volume"] = -1
    elif fault == "bool_price":
        bars["open"] = bars.open.astype(object)
        bars.loc[5, "open"] = True
    elif fault == "bad_geometry":
        bars.loc[5, "low"] = 102
    elif fault == "gap_same_segment":
        bars.loc[5:, "open_time"] += pd.Timedelta(hours=1)
        bars["segment_id"] = 0
    elif fault == "segment_reappears":
        bars["segment_id"] = 0
        bars.loc[5, "segment_id"] = 1
    else:
        bars["segment_id"] = False
    with pytest.raises(ValueError):
        add_features(bars)


def test_empty_and_constant_price_have_complete_schema_and_no_candidate():
    empty = pd.DataFrame(columns=BAR_COLUMNS)
    result = add_features(empty)
    assert list(detect_candidates(result).columns) == CANDIDATE_COLUMNS
    assert detect_candidates(result).empty
    bars = flat_bars()
    bars[["open", "high", "low", "close"]] = 100.
    bars.volume = 0.
    featured = add_features(bars)
    assert featured.atr14.iloc[13:].eq(0).all()
    assert detect_candidates(featured).empty


def test_feature_collision_missing_features_and_bad_identity_reject():
    featured = add_features(real_witness())
    with pytest.raises(ValueError, match="overwrite"):
        add_features(featured)
    with pytest.raises(ValueError, match="missing owner-source"):
        detect_candidates(featured.drop(columns="sma120"))
    with pytest.raises(ValueError, match="venue"):
        detect_candidates(featured, venue=" ")
    with pytest.raises(ValueError, match="symbol"):
        detect_candidates(featured, symbol=" BTCUSDT")
    duplicate = pd.concat([featured, featured])
    with pytest.raises(ValueError, match="unique"):
        detect_candidates(duplicate)
