"""Causal boundary tests for the low-timeframe V8 research runner."""

import pandas as pd

from yoyo.evaluation.spike_v8_lowtf_study import _membership_mask


def test_previous_day_leaderboard_waits_for_first_new_day_bar_close() -> None:
    day = pd.Timestamp("2026-03-22T00:00:00Z")
    membership = {day: {"RIVERUSDT"}}
    signal_bar_opens = pd.DatetimeIndex(
        [
            "2026-03-21T23:55:00Z",  # closes at rank publication time: too early
            "2026-03-22T00:00:00Z",  # closes five minutes after publication: eligible
        ]
    )

    result = _membership_mask(signal_bar_opens, membership, "RIVERUSDT", 5)

    assert result.tolist() == [False, True]


def test_previous_day_leaderboard_rejects_nonmember() -> None:
    day = pd.Timestamp("2026-03-22T00:00:00Z")
    result = _membership_mask(
        pd.DatetimeIndex(["2026-03-22T00:00:00Z"]),
        {day: {"RIVERUSDT"}},
        "BTCUSDT",
        5,
    )

    assert result.tolist() == [False]
