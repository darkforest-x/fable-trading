"""Synthetic timing, missingness and interpretation checks; no exchange IO."""
import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.data.altcoin_derivatives import build_derivative_features


INSTRUMENT = "TEST-USDT-SWAP"
ORIGIN = pd.Timestamp("2024-01-01", tz="UTC")


def decisions(*hours):
    return pd.DatetimeIndex([ORIGIN + pd.Timedelta(hours=h) for h in hours])


def source(kind, n=12):
    times = pd.date_range(ORIGIN, periods=n, freq="8h" if kind == "funding" else "4h")
    result = pd.DataFrame({"inst_id": INSTRUMENT, "event_time": times,
                           "available_at_nominal": times + pd.Timedelta(hours=0 if kind == "funding" else 4)})
    if kind == "oi":
        result["oi_base"] = 100.0 + np.arange(n) * 10
        result["oi_usd"] = result.oi_base * 50
    elif kind == "taker":
        result["buy_base"] = 10.0 + np.arange(n)
        result["sell_base"] = 5.0 + np.arange(n)
    else:
        result["funding_time"] = times
        result["realized_rate"] = np.arange(n) * 0.0001
        result["predicted_rate"] = 100.0  # Deliberately unfit to substitute.
        result["rate_status"] = "actual_available"
    return result


def build(times, **frames):
    return build_derivative_features(times, inst_id=INSTRUMENT, **frames)


def test_current_bucket_is_hidden_and_exact_conservative_boundary_is_allowed():
    result = build(decisions(0, 4, 7, 8, 11, 12), oi=source("oi"), taker=source("taker"))
    assert result.oi_base.iloc[:3].isna().all()
    assert result.taker_buy_share.iloc[:3].isna().all()
    assert result.oi_event_time.iloc[3] == ORIGIN
    assert result.oi_available_at.iloc[3] == ORIGIN + pd.Timedelta(hours=8)
    assert result.oi_age_hours.iloc[3] == 0
    assert result.oi_base.iloc[3] == result.oi_base.iloc[4] == 100
    assert result.oi_base.iloc[5] == 110
    assert result.oi_event_time.iloc[5] == ORIGIN + pd.Timedelta(hours=4)
    assert not result.attrs["historical_publication_time_known"]


def test_four_hour_decisions_use_the_same_availability_clock():
    full = build(decisions(*range(0, 40)), oi=source("oi"), taker=source("taker"), funding=source("funding"))
    sparse = build(decisions(*range(0, 40, 4)), oi=source("oi"), taker=source("taker"), funding=source("funding"))
    assert_frame_equal(full.iloc[::4], sparse)


def test_staleness_is_measured_from_lagged_availability_and_never_backfills():
    result = build(decisions(7, 8, 16, 17, 40), oi=source("oi", 1), taker=source("taker", 1))
    assert result.oi_fresh.to_list() == [False, True, True, False, False]
    assert result.taker_fresh.to_list() == [False, True, True, False, False]
    assert result.oi_base.iloc[3:].isna().all()
    assert result.taker_buy_share.iloc[3:].isna().all()
    assert result.oi_age_hours.iloc[3] == 9
    assert result.oi_event_time.iloc[3] == ORIGIN  # Provenance is retained.


def test_oi_changes_use_exact_four_and_twenty_four_hour_endpoints():
    oi = source("oi")
    result = build(decisions(8, 12, 32), oi=oi)
    assert np.isnan(result.oi_base_log_change_4h.iloc[0])
    assert result.oi_base_log_change_4h.iloc[1] == pytest.approx(np.log(110 / 100))
    assert result.oi_base_log_change_24h.iloc[2] == pytest.approx(np.log(160 / 100))
    missing_four = oi.drop(index=5)  # No 20h endpoint for the selected 24h row.
    changed = build(decisions(32), oi=missing_four)
    assert np.isnan(changed.oi_base_log_change_4h.iloc[0])
    assert changed.oi_base_log_change_24h.iloc[0] == pytest.approx(np.log(160 / 100))
    missing_day = oi.drop(index=0)
    assert np.isnan(build(decisions(32), oi=missing_day).oi_base_log_change_24h.iloc[0])


def test_price_only_dollar_oi_change_does_not_invent_base_oi_growth():
    oi = source("oi")
    oi["oi_base"] = 100.0
    result = build(decisions(12, 32), oi=oi)
    assert result.oi_base_log_change_4h.eq(0).all()
    assert result.oi_base_log_change_24h.iloc[1] == 0
    assert result.oi_usd_log_change_4h.gt(0).all()
    assert result.oi_usd_log_change_24h.iloc[1] > 0
    assert not any("long" in name or "short" in name for name in result.columns)


def test_zero_missing_oi_and_zero_taker_total_are_not_filled_or_infinite():
    oi, taker = source("oi"), source("taker")
    oi.loc[0, "oi_base"] = 0
    oi.loc[1, "oi_base"] = np.nan
    taker.loc[0, ["buy_base", "sell_base"]] = 0
    result = build(decisions(8, 12, 16), oi=oi, taker=taker)
    assert result.oi_base_log_change_4h.isna().all()
    assert np.isnan(result.oi_base.iloc[1])  # No fallback to row zero.
    assert np.isnan(result.taker_buy_share.iloc[0])
    assert not np.isinf(result.select_dtypes(include="number")).any().any()


def test_taker_share_and_imbalance_use_buy_and_sell_with_correct_sign():
    taker = source("taker", 3)
    taker["buy_base"] = [30, 10, 0]
    taker["sell_base"] = [10, 30, 20]
    result = build(decisions(8, 12, 16), taker=taker)
    np.testing.assert_allclose(result.taker_buy_share, [0.75, 0.25, 0])
    np.testing.assert_allclose(result.taker_imbalance, [0.5, -0.5, -1])


def test_funding_uses_actual_settlement_plus_one_hour_not_future_actual_or_prediction():
    funding = source("funding", 4)
    funding["realized_rate"] = [-0.001, 0, 0.002, np.nan]
    funding.loc[3, "rate_status"] = "predicted_only"
    result = build(decisions(0, 1, 8, 9, 16, 17, 24, 25), funding=funding)
    expected = [np.nan, -0.001, -0.001, 0, 0, 0.002, 0.002, np.nan]
    np.testing.assert_allclose(result.funding_last_rate, expected, equal_nan=True)
    assert result.funding_fresh.iloc[-1]  # Available but actual is missing.
    assert result.funding_24h_missing_count.iloc[-1] == 1
    assert np.isnan(result.funding_24h_sum.iloc[-1])


def test_trailing_funding_sum_excludes_unpublished_settlement_and_requires_span():
    funding = source("funding", 6)
    funding["realized_rate"] = [0.001, 0.002, -0.004, 0.003, 0.004, 0.005]
    result = build(decisions(17, 24, 25), funding=funding)
    assert not result.funding_history_covers_24h.iloc[0]
    assert np.isnan(result.funding_24h_sum.iloc[0])
    assert result.funding_24h_actual_count.to_list() == [3, 2, 3]
    assert result.funding_24h_sum.iloc[1] == pytest.approx(0.002 - 0.004)
    assert result.funding_24h_sum.iloc[2] == pytest.approx(0.002 - 0.004 + 0.003)


def test_funding_staleness_masks_numeric_features_and_preserves_source_clock():
    funding = source("funding", 1)
    result = build(decisions(1, 9, 10), funding=funding)
    assert result.funding_fresh.to_list() == [True, True, False]
    assert result.funding_last_rate.iloc[:2].eq(0).all()
    assert np.isnan(result.funding_last_rate.iloc[2])
    assert np.isnan(result.funding_24h_actual_count.iloc[2])
    assert result.funding_event_time.iloc[2] == ORIGIN


def test_future_mutation_and_future_source_truncation_leave_prior_decisions_unchanged():
    frames = {kind: source(kind) for kind in ("oi", "taker", "funding")}
    before = {kind: frame.copy(deep=True) for kind, frame in frames.items()}
    result = build(decisions(*range(0, 48)), **frames)
    changed = {kind: frame.copy(deep=True) for kind, frame in frames.items()}
    changed["oi"].loc[changed["oi"].event_time >= ORIGIN + pd.Timedelta(hours=24), ["oi_base", "oi_usd"]] *= 100
    changed["taker"].loc[changed["taker"].event_time >= ORIGIN + pd.Timedelta(hours=24), "buy_base"] *= 50
    changed["funding"].loc[changed["funding"].event_time >= ORIGIN + pd.Timedelta(hours=24), "realized_rate"] = -100
    assert_frame_equal(result.iloc[:24], build(decisions(*range(0, 24)), **changed))
    for end in (12, 24, 40):
        cut = {kind: frame[frame.event_time < ORIGIN + pd.Timedelta(hours=end)] for kind, frame in frames.items()}
        assert_frame_equal(result.iloc[:end], build(decisions(*range(end)), **cut))
    for kind in frames:
        assert_frame_equal(frames[kind], before[kind])


def test_absent_sources_and_empty_decisions_are_supported_without_inventing_zeros():
    result = build(decisions(4, 8, 12))
    assert not result[["oi_fresh", "taker_fresh", "funding_fresh"]].any().any()
    assert result[["oi_base", "taker_buy_share", "funding_last_rate", "funding_24h_sum"]].isna().all().all()
    empty = pd.DatetimeIndex([], tz="UTC")
    assert build(empty, oi=source("oi"), funding=source("funding")).empty


@pytest.mark.parametrize("kind,column", [("oi", "oi_base"), ("oi", "oi_usd"), ("taker", "buy_base"), ("taker", "sell_base")])
@pytest.mark.parametrize("value", [-1, np.inf, -np.inf])
def test_invalid_oi_and_volume_are_rejected(kind, column, value):
    frame = source(kind)
    frame.loc[1, column] = value
    with pytest.raises(ValueError):
        build(decisions(12), **{kind: frame})


@pytest.mark.parametrize("bad", ["symbol", "unix", "naive", "duplicate", "unsorted", "nominal", "alignment", "settlement", "status"])
def test_ambiguous_source_schema_and_clock_fail_closed(bad):
    kind = "funding" if bad in ("settlement", "status") else "oi"
    frame = source(kind)
    if bad == "symbol": frame.loc[1, "inst_id"] = "OTHER-USDT-SWAP"
    elif bad == "unix": frame["event_time"] = pd.DatetimeIndex(frame.event_time).asi8 // 1_000_000
    elif bad == "naive": frame["event_time"] = frame.event_time.dt.tz_localize(None)
    elif bad == "duplicate": frame = pd.concat([frame, frame.iloc[[-1]]])
    elif bad == "unsorted": frame = frame.iloc[::-1]
    elif bad == "nominal": frame["available_at_nominal"] += pd.Timedelta(hours=1)
    elif bad == "alignment":
        frame["event_time"] += pd.Timedelta(hours=1)
        frame["available_at_nominal"] += pd.Timedelta(hours=1)
    elif bad == "settlement": frame["funding_time"] += pd.Timedelta(hours=1)
    elif bad == "status": frame.loc[1, "rate_status"] = "predicted_only"
    with pytest.raises(ValueError):
        build(decisions(12), **{kind: frame})


@pytest.mark.parametrize("bad", ["naive", "duplicates", "unaligned", "unordered"])
def test_invalid_decision_clocks_are_rejected(bad):
    times = decisions(4, 8, 12)
    if bad == "naive": times = times.tz_localize(None)
    elif bad == "duplicates": times = times.append(times[[-1]])
    elif bad == "unaligned": times += pd.Timedelta(minutes=5)
    elif bad == "unordered": times = times[::-1]
    with pytest.raises(ValueError):
        build(times, oi=source("oi"))
