"""Synthetic calendar, admission, and matching checks without source-market reads."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_v8_ict import (
    entry_times,
    filter_opportunities,
    filter_then_serial,
    matched_controls,
    session_annotation,
    session_mask,
)


@pytest.mark.parametrize("date", ["2025-03-09", "2025-11-02"])
def test_dst_entry_boundaries_include_seconds_and_weekends(date):
    local = pd.DatetimeIndex([
        f"{date} 01:59:59", f"{date} 02:00:00", f"{date} 04:59:59",
        f"{date} 05:00:00", f"{date} 07:59:59", f"{date} 08:00:00",
        f"{date} 10:59:59", f"{date} 11:00:00",
    ]).tz_localize("America/New_York", nonexistent="shift_forward", ambiguous=True).tz_convert("UTC")
    assert session_annotation(local).tolist() == [
        "outside", "london", "london", "lunch", "lunch", "new_york", "new_york", "outside",
    ]
    assert session_mask(local, "union").tolist() == [False, True, True, True, True, True, True, False]
    assert session_mask(local, "all").tolist() == [True] * 8


@pytest.mark.parametrize("minutes", [3, 5, 15, 30, 60, 240])
def test_actual_next_observed_entry_drives_each_timeframe(minutes):
    entry = pd.Timestamp("2025-03-10 06:00:00+00:00")  # 02:00 New York after DST start.
    frame = pd.DataFrame({"atr": [1., 1.], "close": [100., 100.]}, index=pd.DatetimeIndex([
        entry - pd.Timedelta(minutes=minutes), entry,
    ], name="open_time"))
    actual = entry_times(frame)
    assert actual[0] == entry and pd.isna(actual[1])
    assert session_mask([actual[0]], "union").tolist() == [True]


def _row(signal_i, entry, exit_i, *, side=1, censored=False, net_r=-1.):
    return dict(
        signal_i=signal_i, entry_i=signal_i + 1, entry_time=entry, exit_i=exit_i,
        exit_time=pd.Timestamp(entry) + pd.Timedelta(hours=12), exit_at_open=False,
        side=side, censored=censored, net_r=net_r, gross_r=net_r + .2,
    )


def test_filtering_precedes_serial_and_does_not_force_session_close():
    outside = _row(0, "2025-03-10T05:59:00Z", 30)
    first = _row(10, "2025-03-10T06:00:00Z", 15)
    later = _row(20, "2025-03-10T06:10:00Z", 25)
    assert [row["signal_i"] for row in filter_then_serial([outside, first, later], "all")] == [0]
    accepted = filter_then_serial([outside, first, later], "union")
    assert [row["signal_i"] for row in accepted] == [10, 20]
    filtered = filter_opportunities([first], "union")
    assert filtered[0] is first and filtered[0]["exit_i"] == 15
    assert filtered[0]["exit_time"] == first["exit_time"]


def _matching_frame():
    index = pd.date_range("2025-03-10T06:00:00Z", periods=160, freq="min", name="open_time")
    return pd.DataFrame({"atr": np.ones(len(index)), "close": np.full(len(index), 100.)}, index=index)


def test_controls_match_actual_entry_window_and_keep_partial_receipts():
    frame = _matching_frame()
    entries = entry_times(frame)
    target_i = 140
    target = _row(target_i, entries[target_i], 145, net_r=1.)
    calls = []

    def replay(i, policy, side):
        calls.append((i, policy, side))
        if i == 141:
            return None
        if i == 142:
            return dict(net_r=.5, censored=True)
        return dict(net_r=.25, censored=False)

    detail, metrics = matched_controls(
        frame, [target], {"frozen": True}, policy_key="arrows_augment_profit", session="union",
        start=entries[130], end=entries[150], seed=7, replay_fn=replay, cache={},
    )
    row = detail.iloc[0]
    assert row["status"] in {"complete", "partial"}
    assert row["matched"] <= 5
    assert all(130 <= i < 150 for i, _, _ in calls)
    assert metrics["matched_n"] + metrics["matched_missing"] == 1
    assert metrics["months"] in {0, 1}


def test_censored_target_is_receipted_not_counted_as_a_full_control():
    frame = _matching_frame()
    entries = entry_times(frame)
    target = _row(140, entries[140], 145, censored=True, net_r=2.)
    detail, metrics = matched_controls(
        frame, [target], None, policy_key="original", session="union",
        start=entries[130], end=entries[150], seed=7,
        replay_fn=lambda i, policy, side: dict(net_r=.1, censored=False),
    )
    assert detail.iloc[0]["status"] == "target_censored"
    assert metrics["matched_n"] == metrics["matched_missing"] == 0
    assert metrics["target_censored"] == 1


def test_empty_accepted_list_has_stable_detail_schema_and_zero_metrics():
    frame = _matching_frame()
    entries = entry_times(frame)
    detail, metrics = matched_controls(
        frame, [], None, policy_key="original", session="union",
        start=entries[130], end=entries[150], seed=7,
        replay_fn=lambda i, policy, side: pytest.fail("empty target list must not replay controls"),
    )
    assert detail.empty
    assert {"status", "draw_receipts", "matched"}.issubset(detail.columns)
    assert metrics["matched_n"] == metrics["matched_missing"] == metrics["months"] == 0
    assert metrics["random_mean_net_r"] is metrics["excess_net_r"] is metrics["p"] is None


def test_shared_replay_cache_is_equivalent_and_suppresses_repeat_calls():
    frame = _matching_frame()
    entries = entry_times(frame)
    target = _row(140, entries[140], 145, net_r=1.)
    calls = []

    def replay(i, policy, side):
        calls.append((i, policy, side))
        return dict(net_r=float(i) / 1000., censored=False)

    cache = {}
    kwargs = dict(
        policy_key="arrows_augment_profit", session="union", start=entries[130], end=entries[150],
        seed=7, replay_fn=replay, cache=cache,
    )
    first_detail, first_metrics = matched_controls(frame, [target], {"frozen": True}, **kwargs)
    first_calls = len(calls)
    second_detail, second_metrics = matched_controls(frame, [target], {"frozen": True}, **kwargs)
    pd.testing.assert_frame_equal(first_detail, second_detail)
    assert first_metrics == second_metrics
    assert first_calls == 5 and len(calls) == first_calls
