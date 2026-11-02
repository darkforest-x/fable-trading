from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation import h1_outside_glyph_signals as signals


NY = "America/New_York"


def _bars(start: str, end: str) -> pd.DataFrame:
    start_utc = pd.Timestamp(start, tz=NY).tz_convert("UTC")
    end_utc = pd.Timestamp(end, tz=NY).tz_convert("UTC")
    index = pd.date_range(start_utc, end_utc, freq="5min", inclusive="left")
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        index=index,
    )


def _dst_week() -> tuple[pd.DataFrame, pd.Timestamp]:
    frame = _bars("2026-03-04 00:00", "2026-03-16 00:00")
    monday_start = pd.Timestamp("2026-03-09 00:00", tz=NY).tz_convert("UTC")
    # A high that belongs to Monday in UTC but Sunday in New York must not
    # leak into the New York Monday range.
    utc_monday_only = pd.Timestamp("2026-03-09 02:00", tz="UTC")
    frame.loc[utc_monday_only, "high"] = 250.0
    frame.loc[monday_start + pd.Timedelta(hours=12), "high"] = 150.0
    frame.loc[monday_start + pd.Timedelta(hours=12, minutes=5), "low"] = 70.0
    return frame, monday_start


def test_new_york_monday_range_uses_complete_288_bar_dst_aware_anchor() -> None:
    frame, monday_start = _dst_week()
    monday_end = pd.Timestamp("2026-03-10 00:00", tz=NY).tz_convert("UTC")
    monday = frame.loc[(frame.index >= monday_start) & (frame.index < monday_end)]
    assert len(monday) == 288
    assert monday.index[0] == monday_start

    h1 = signals.prepare(frame)
    target = pd.Timestamp("2026-03-10 12:00", tz=NY).tz_convert("UTC")
    row = h1.loc[target]
    assert row.week == "2026-03-09"
    assert row.monday_high == 150.0
    assert row.monday_low == 70.0
    assert row.upper == 70.0 + 1.618 * 80.0
    assert row.lower == 150.0 - 1.618 * 80.0
    assert row.eligible
    assert not h1.loc[pd.Timestamp("2026-03-09 12:00", tz=NY).tz_convert("UTC"), "eligible"]
    assert not h1.loc[pd.Timestamp("2026-03-14 12:00", tz=NY).tz_convert("UTC"), "eligible"]


def test_incomplete_monday_disables_weekly_levels_and_eligibility() -> None:
    frame, monday_start = _dst_week()
    frame = frame.drop(monday_start + pd.Timedelta(hours=7, minutes=25))
    h1 = signals.prepare(frame)
    target = pd.Timestamp("2026-03-10 12:00", tz=NY).tz_convert("UTC")
    assert pd.isna(h1.loc[target, "monday_high"])
    assert pd.isna(h1.loc[target, "monday_low"])
    assert not h1.loc[target, "eligible"]
    assert not h1.signal_long.any() and not h1.signal_short.any()


def test_only_complete_h1_bars_survive_and_50_bar_warmup_restarts_after_gap() -> None:
    frame = _bars("2026-03-02 00:00", "2026-03-07 00:00")
    gap_hour = pd.Timestamp("2026-03-04 12:00", tz="UTC")
    frame = frame.drop(gap_hour + pd.Timedelta(minutes=25))
    h1 = signals.prepare(frame)

    assert gap_hour not in h1.index
    first_after_gap = gap_hour + pd.Timedelta(hours=1)
    forty_ninth = first_after_gap + pd.Timedelta(hours=48)
    fiftieth = first_after_gap + pd.Timedelta(hours=49)
    assert not h1.loc[forty_ninth, "eligible"]
    assert h1.loc[fiftieth, "eligible"]
    assert h1.index.to_series().diff().eq(pd.Timedelta(hours=1)).loc[fiftieth]


def test_future_truncation_and_future_poisoning_do_not_change_past_features() -> None:
    frame, _ = _dst_week()
    target = pd.Timestamp("2026-03-11 04:00", tz="UTC")
    end = target + pd.Timedelta(hours=1)
    through_target = frame.loc[frame.index < end]
    poisoned = frame.copy()
    future = poisoned.index >= end
    poisoned.loc[future, ["open", "high", "low", "close"]] = 250.0
    poisoned.loc[future, "volume"] = 1000.0

    full = signals.prepare(frame)
    truncated = signals.prepare(through_target)
    changed_future = signals.prepare(poisoned)
    expected = full.loc[full.index <= target]
    pd.testing.assert_frame_equal(expected, truncated.loc[truncated.index <= target])
    pd.testing.assert_frame_equal(expected, changed_future.loc[changed_future.index <= target])


def test_unclosed_four_hour_candle_does_not_change_h1_trend() -> None:
    frame, _ = _dst_week()
    target = pd.Timestamp("2026-03-11 04:00", tz="UTC")
    target_close = target + pd.Timedelta(hours=1)
    baseline = signals.prepare(frame)

    changed = frame.copy()
    # H1 bars after the target close but before the 04:00-08:00 UTC candle
    # closes can change that future 4H result, never the target H1 trend.
    unfinished = (changed.index >= target_close) & (changed.index < target + pd.Timedelta(hours=4))
    changed.loc[unfinished, ["open", "high", "low", "close"]] = 1.0
    changed_result = signals.prepare(changed)
    assert baseline.loc[target, "eligible"]
    assert baseline.loc[target, "trend4h_long"] == changed_result.loc[target, "trend4h_long"]
    assert baseline.loc[target, "trend4h_short"] == changed_result.loc[target, "trend4h_short"]


def test_outside_signal_columns_are_strict_wick_gated_arrows() -> None:
    frame, _ = _dst_week()
    h1 = signals.prepare(frame)
    assert h1.eligible.dtype == bool
    assert h1.week.dtype.name == "string"
    expected_long = h1.eligible & h1.arrow_long & (h1.low < h1.lower)
    expected_short = h1.eligible & h1.arrow_short & (h1.high > h1.upper)
    assert h1.signal_long.equals(expected_long.fillna(False))
    assert h1.signal_short.equals(expected_short.fillna(False))
    assert pd.DatetimeIndex(h1.close_at).equals(h1.index + pd.Timedelta(hours=1))


def test_short_wick_must_strictly_exceed_upper_1618(monkeypatch) -> None:
    frame, _ = _dst_week()
    monday_high, monday_low = 150.0, 70.0
    upper = monday_low + 1.618 * (monday_high - monday_low)
    at_level = pd.Timestamp("2026-03-10 12:00", tz=NY).tz_convert("UTC")
    beyond_level = at_level + pd.Timedelta(hours=1)
    frame.loc[at_level, "high"] = upper
    frame.loc[beyond_level, "high"] = upper + 0.01

    def arrows(h1: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(False, index=h1.index, columns=["arrow_long", "arrow_short"])
        if at_level in result.index:
            result.loc[at_level, "arrow_short"] = True
        if beyond_level in result.index:
            result.loc[beyond_level, "arrow_short"] = True
        return result

    monkeypatch.setattr(signals, "compute_signals", arrows)
    h1 = signals.prepare(frame)
    assert h1.loc[at_level, "eligible"] and h1.loc[beyond_level, "eligible"]
    assert not h1.loc[at_level, "signal_short"]
    assert h1.loc[beyond_level, "signal_short"]
