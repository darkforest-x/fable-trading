from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.h1_ema_cloud_study import cloud_features, schedule


FIVE_MINUTES = pd.Timedelta(minutes=5)
BASE_ENTRY_I = 4


def _frame(closes: list[float] | None = None, *, periods: int = 32) -> pd.DataFrame:
    if closes is None:
        closes = [100.0] * periods
    index = pd.date_range("2026-01-05 00:00", periods=len(closes), freq=FIVE_MINUTES, tz="UTC")
    close = np.asarray(closes, dtype=float)
    open_ = close.copy()
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) + 0.5,
            "low": np.minimum(open_, close) - 0.5,
            "close": close,
            "volume": 1.0,
        },
        index=index,
    )


def _cloud(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ema_fast": 101.0,
            "ema_slow": 100.0,
            "flip_long": False,
            "flip_short": False,
        },
        index=frame.index,
    )


def _signal(frame: pd.DataFrame, side: int = 1, *, target: float | None = None,
            signal_high: float = 101.0, signal_low: float = 99.0) -> pd.DataFrame:
    if target is None:
        target = 120.0 if side == 1 else 80.0
    return pd.DataFrame(
        [
            {
                "pair_id": f"pair-{side}",
                "entry_time": str(frame.index[BASE_ENTRY_I]),
                "entry_i": BASE_ENTRY_I,
                "side": side,
                "target_price": target,
                "signal_high": signal_high,
                "signal_low": signal_low,
            }
        ]
    )


def _schedule(frame: pd.DataFrame, cloud: pd.DataFrame, side: int = 1,
              *, target: float | None = None, signal_high: float = 101.0,
              signal_low: float = 99.0, wait_hours: int = 24) -> pd.Series:
    result = schedule(
        frame,
        _signal(frame, side, target=target, signal_high=signal_high, signal_low=signal_low),
        cloud,
        {"wait_hours": wait_hours, "wick_buffer": 0.001},
    )
    assert len(result) == 1
    return result.iloc[0]


@pytest.mark.parametrize(
    ("closes", "flip_column", "flip_i"),
    [
        ([10, 9, 8, 10, 12], "flip_long", 3),
        ([10, 11, 12, 10, 8], "flip_short", 3),
    ],
)
def test_cloud_crosses_use_closed_prices_and_wait_for_slow_ema_warmup(
    closes: list[float], flip_column: str, flip_i: int,
) -> None:
    frame = _frame(closes)
    cloud = cloud_features(frame, fast=2, slow=3)

    assert cloud.index.equals(frame.index)
    assert list(cloud.columns) == ["ema_fast", "ema_slow", "flip_long", "flip_short"]
    assert cloud["ema_slow"].iloc[:2].isna().all()
    assert not cloud[["flip_long", "flip_short"]].iloc[:2].to_numpy().any()
    assert bool(cloud.loc[frame.index[flip_i], flip_column])
    assert not cloud[["flip_long", "flip_short"]].iloc[flip_i + 1 :].to_numpy().any()


def test_cloud_ema_resets_after_gap_and_uses_close_only() -> None:
    first = _frame([100, 101, 102, 103, 104])
    second_index = pd.date_range(first.index[-1] + pd.Timedelta(minutes=10), periods=5,
                                 freq=FIVE_MINUTES)
    second_close = np.array([10, 11, 12, 10, 8], dtype=float)
    second = pd.DataFrame(
        {"open": second_close, "high": second_close + 1, "low": second_close - 1,
         "close": second_close, "volume": 1.0},
        index=second_index,
    )
    combined = pd.concat([first, second])

    actual = cloud_features(combined, fast=2, slow=3)
    isolated = cloud_features(second, fast=2, slow=3)
    pd.testing.assert_frame_equal(actual.loc[second.index], isolated, check_freq=False)

    changed_wicks = combined.copy()
    changed_wicks[["open", "high", "low"]] *= 50
    wick_only = cloud_features(changed_wicks, fast=2, slow=3)
    pd.testing.assert_frame_equal(actual, wick_only)


def test_cloud_features_are_prefix_causal_under_truncation_and_future_poisoning() -> None:
    frame = _frame([10, 9, 8, 10, 12, 14, 13, 11, 8, 7, 9, 12, 15, 14, 13])
    target_i = 9
    full = cloud_features(frame, fast=2, slow=3)
    truncated = cloud_features(frame.iloc[: target_i + 1], fast=2, slow=3)
    poisoned_frame = frame.copy()
    poisoned_frame.iloc[target_i + 1 :, poisoned_frame.columns.get_loc("close")] = 1000.0
    poisoned = cloud_features(poisoned_frame, fast=2, slow=3)

    expected = full.iloc[: target_i + 1]
    pd.testing.assert_frame_equal(expected, truncated)
    pd.testing.assert_frame_equal(expected, poisoned.iloc[: target_i + 1])


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize(
    ("confirm_offset", "expected_wait"),
    [(BASE_ENTRY_I - 1, 0), (BASE_ENTRY_I + 2, 15)],
)
def test_schedule_enters_at_exact_open_after_first_directional_close(
    side: int, confirm_offset: int, expected_wait: int,
) -> None:
    frame = _frame()
    cloud = _cloud(frame)
    cloud.loc[frame.index[confirm_offset], "flip_long" if side == 1 else "flip_short"] = True

    row = _schedule(frame, cloud, side)

    assert row.status == "entered"
    assert row.confirm_i == confirm_offset
    assert row.entry_i == confirm_offset + 1
    assert row.wait_minutes == expected_wait


def test_schedule_does_not_treat_existing_same_side_cloud_state_as_a_new_flip() -> None:
    frame = _frame(periods=BASE_ENTRY_I + 24 * 12 + 4)
    cloud = _cloud(frame)
    # Fast EMA stays above slow EMA throughout, but there is no new crossover.
    cloud["ema_fast"] = 101.0
    cloud["ema_slow"] = 100.0

    row = _schedule(frame, cloud, 1)

    assert row.status == "no_flip_24h"
    assert row.entry_i == -1


def test_schedule_ignores_opposite_cross_before_first_new_same_side_cross() -> None:
    frame = _frame()
    cloud = _cloud(frame)
    cloud.loc[frame.index[6], "flip_short"] = True
    cloud.loc[frame.index[9], "flip_long"] = True

    row = _schedule(frame, cloud, 1)

    assert row.status == "entered"
    assert row.confirm_i == 9
    assert row.entry_i == 10


def test_target_touch_on_the_confirmation_candle_cancels_delayed_entry() -> None:
    frame = _frame()
    confirm_i = BASE_ENTRY_I + 2
    frame.iloc[confirm_i, frame.columns.get_loc("high")] = 120.0
    cloud = _cloud(frame)
    cloud.loc[frame.index[confirm_i], "flip_long"] = True

    row = _schedule(frame, cloud, 1)

    assert row.status == "target_before_flip"
    assert row.entry_i == -1


def test_schedule_accepts_flip_closing_exactly_at_inclusive_24h_boundary() -> None:
    frame = _frame(periods=BASE_ENTRY_I + 24 * 12 + 2)
    cloud = _cloud(frame)
    confirm_i = BASE_ENTRY_I + 24 * 12 - 1
    cloud.loc[frame.index[confirm_i], "flip_long"] = True

    row = _schedule(frame, cloud, 1)

    assert row.status == "entered"
    assert row.confirm_i == confirm_i
    assert row.entry_i == confirm_i + 1
    assert row.wait_minutes == 24 * 60


def test_schedule_ignores_flip_closing_one_bar_after_24h_boundary() -> None:
    frame = _frame(periods=BASE_ENTRY_I + 24 * 12 + 3)
    cloud = _cloud(frame)
    confirm_i = BASE_ENTRY_I + 24 * 12
    cloud.loc[frame.index[confirm_i], "flip_long"] = True

    row = _schedule(frame, cloud, 1)

    assert row.status == "no_flip_24h"
    assert row.entry_i == -1


def test_schedule_censors_when_a_gap_precedes_a_later_flip() -> None:
    frame = _frame(periods=20).drop(index=_frame(periods=20).index[6])
    cloud = _cloud(frame)
    later_i = frame.index.get_loc(pd.Timestamp("2026-01-05 00:35", tz="UTC"))
    cloud.iloc[later_i, cloud.columns.get_loc("flip_long")] = True

    row = _schedule(frame, cloud, 1)

    assert row.status == "wait_censored"
    assert row.entry_i == -1


def test_schedule_censors_at_data_end_before_wait_expiry() -> None:
    frame = _frame(periods=BASE_ENTRY_I + 8)
    cloud = _cloud(frame)

    row = _schedule(frame, cloud, 1)

    assert row.status == "wait_censored"
    assert row.entry_i == -1


@pytest.mark.parametrize(
    ("side", "entry_open", "target", "signal_high", "signal_low"),
    [
        (1, 121.0, 120.0, 101.0, 99.0),
        (-1, 79.0, 80.0, 101.0, 99.0),
        (1, 100.0, 120.0, 101.0, 101.0),
        (-1, 100.0, 80.0, 99.0, 99.0),
    ],
)
def test_schedule_rejects_delayed_entries_with_invalid_target_or_wick_geometry(
    side: int, entry_open: float, target: float, signal_high: float, signal_low: float,
) -> None:
    frame = _frame()
    confirm_i = BASE_ENTRY_I + 1
    entry_i = confirm_i + 1
    frame.iloc[entry_i, frame.columns.get_loc("open")] = entry_open
    frame.iloc[entry_i, frame.columns.get_loc("high")] = max(entry_open, frame.iloc[entry_i].close) + 0.5
    frame.iloc[entry_i, frame.columns.get_loc("low")] = min(entry_open, frame.iloc[entry_i].close) - 0.5
    cloud = _cloud(frame)
    cloud.loc[frame.index[confirm_i], "flip_long" if side == 1 else "flip_short"] = True

    row = _schedule(frame, cloud, side, target=target,
                    signal_high=signal_high, signal_low=signal_low)

    assert row.status == "invalid_geometry"
    assert row.entry_i == entry_i
    assert row.confirm_i == confirm_i

