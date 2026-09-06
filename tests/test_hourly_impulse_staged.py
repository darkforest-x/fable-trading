"""Synthetic-only checks for original-risk profit banking and takeover clocks."""
import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.hourly_impulse import simulate_events
from yoyo.layers.l3_backtest.hourly_impulse_staged import simulate_staged_events


START = pd.Timestamp("2026-01-01T01:00:00Z")


def raw_bars(count=30):
    return pd.DataFrame({
        "open_time": pd.date_range(START, periods=count, freq="5min"),
        "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0,
        "segment_id": 0,
    })


def management(sides, minutes=15, start=START):
    return pd.DataFrame({
        "open_time": pd.date_range(start, periods=len(sides), freq="{}min".format(minutes)),
        "ma": 100.0, "ma_side": sides, "ma_slope_atr": sides,
        "low": 99.0, "high": 101.0, "close": 100.0, "segment_id": 0,
    })


def entries(direction=1, **overrides):
    row = {"event_id": "one", "decision_time": START, "direction": direction,
           "initial_stop": 90.0 if direction == 1 else 110.0,
           "signal_atr": 2.0, "preserved_feature": 0.7}
    row.update(overrides)
    return pd.DataFrame([row])


def replay(raw=None, mg15=None, mg60=None, events=None, cutoff=None, **policy):
    return simulate_staged_events(
        raw_bars() if raw is None else raw,
        management([-1] * 9) if mg15 is None else mg15,
        management([-1] * 3, 60) if mg60 is None else mg60,
        entries() if events is None else events,
        policy, end_exclusive=cutoff,
    ).iloc[0]


def mirror(raw):
    mirrored = raw.copy()
    mirrored["open"] = 200 - raw.open
    mirrored["high"] = 200 - raw.low
    mirrored["low"] = 200 - raw.high
    mirrored["close"] = 200 - raw.close
    return mirrored


@pytest.mark.parametrize("direction", [1, -1])
@pytest.mark.parametrize("cutoff", [None, START + pd.Timedelta(minutes=12)])
def test_empty_policy_is_exact_baseline_including_schema(direction, cutoff):
    raw = raw_bars()
    mg = management([-direction] * 8)
    events = entries(direction)
    expected = simulate_events(raw, mg, events, {}, end_exclusive=cutoff)
    actual = simulate_staged_events(raw, mg, pd.DataFrame(), events,
                                    {"partial_targets": [], "takeover_r": None}, end_exclusive=cutoff)
    pd.testing.assert_frame_equal(actual, expected)


@pytest.mark.parametrize("direction", [1, -1])
def test_multiple_targets_original_fraction_and_mirrored_returns(direction):
    raw = raw_bars()
    raw.loc[1, ["high", "close"]] = [121, 118]
    raw.loc[3, ["open", "high", "low", "close"]] = [115, 116, 114, 115]
    if direction == -1:
        raw = mirror(raw)
    result = replay(raw=raw, mg15=management([-direction] * 5), events=entries(direction),
                    partial_targets=[[1, 0.25], [2, 0.25]])
    assert result.partial_fill_count == 2
    assert result.partial_fraction == pytest.approx(0.5)
    assert result.exit_remaining_fraction == pytest.approx(0.5)
    assert result.realised_partial_gross_return == pytest.approx(0.075)
    assert result.realised_partial_net_return == pytest.approx(0.074)
    assert result.gross_return == pytest.approx(0.15)
    assert result.net_return == pytest.approx(0.148)
    assert result.net_r == pytest.approx(1.48)
    assert result.partial_exit_price == pytest.approx(115 if direction == 1 else 85)
    assert result.initial_stop == (90 if direction == 1 else 110)
    assert result.preserved_feature == 0.7


def test_targets_fill_once_despite_repeated_touches():
    raw = raw_bars()
    raw.loc[1:8, "high"] = 115
    result = replay(raw=raw, mg15=management([1, 1, -1]), partial_targets=[[1, 0.25]])
    assert result.partial_fill_count == 1
    assert result.partial_fraction == 0.25
    assert result.partial_exit_time == START + pd.Timedelta(minutes=10)


def test_same_bar_stop_beats_targets_and_takeover_with_conservative_excursion():
    raw = raw_bars()
    raw.loc[0, ["high", "low"]] = [140, 80]
    result = replay(raw=raw, partial_targets=[[1, 0.25], [2, 0.25]], takeover_r=1)
    assert result.outcome == "hard_stop"
    assert result.partial_fill_count == 0
    assert result.max_favourable_r == 0
    assert result.max_adverse_r == -1
    assert pd.isna(result.takeover_trigger_time)
    assert result.net_return == pytest.approx(-0.102)


def test_resting_gap_target_fills_before_later_intrabar_stop():
    raw = raw_bars()
    raw.loc[1, ["open", "high", "low", "close"]] = [120, 121, 80, 100]
    result = replay(raw=raw, partial_targets=[[1, 0.25]])
    assert result.partial_fills[0]["price"] == 120
    assert result.partial_fills[0]["time"] == START + pd.Timedelta(minutes=5)
    assert result.outcome == "hard_stop"
    assert result.realised_partial_gross_return == pytest.approx(0.05)
    assert result.net_return == pytest.approx(-0.027)


def test_gap_stop_beats_same_timestamp_management():
    raw = raw_bars()
    raw.loc[3, ["open", "high", "low", "close"]] = [88, 90, 87, 89]
    result = replay(raw=raw, partial_targets=[[1, 0.25]], takeover_r=1)
    assert result.outcome == "hard_stop_gap"
    assert result.exit_price == 88
    assert result.partial_fill_count == 0


def test_resting_gap_targets_fill_before_same_timestamp_colour_market_exit():
    raw = raw_bars()
    raw.loc[3, ["open", "high", "low", "close"]] = [130, np.nan, np.nan, np.nan]
    result = replay(raw=raw, partial_targets=[[1, 0.25], [2, 0.25]], takeover_r=1)
    assert result.outcome == "colour_exit"
    assert result.exit_time == START + pd.Timedelta(minutes=15)
    assert result.partial_fill_count == 2
    assert all(fill["price"] == 130 for fill in result.partial_fills)
    assert result.net_return == pytest.approx(0.298)
    assert not result.takeover_active


def test_all_target_fractions_close_trade_without_counting_unseen_later_high():
    raw = raw_bars()
    raw.loc[0, "high"] = 135
    result = replay(raw=raw, partial_targets=[[1, 0.5], [2, 0.5]])
    assert result.outcome == "partial_targets_complete"
    assert result.exit_remaining_fraction == 0
    assert result.partial_fraction == 1
    assert result.max_favourable_r == 2
    assert result.gross_return == pytest.approx(0.15)
    assert result.net_return == pytest.approx(0.148)


def test_threshold_cannot_cancel_colour_at_its_activation_timestamp():
    raw = raw_bars()
    raw.loc[2, "high"] = 121
    result = replay(raw=raw, takeover_r=2)
    assert result.outcome == "colour_exit"
    assert result.exit_time == START + pd.Timedelta(minutes=15)
    assert result.takeover_trigger_time == START + pd.Timedelta(minutes=15)
    assert pd.isna(result.takeover_time)
    assert not result.takeover_active
    assert result.exit_management_minutes == 15


def test_takeover_activates_next_open_and_waits_for_completed_hour():
    raw = raw_bars()
    raw.loc[1, "high"] = 121
    result = replay(raw=raw, takeover_r=2)
    assert result.takeover_trigger_time == START + pd.Timedelta(minutes=10)
    assert result.takeover_time == START + pd.Timedelta(minutes=10)
    assert result.takeover_active
    assert result.exit_time == START + pd.Timedelta(minutes=60)
    assert result.exit_management_minutes == 60
    assert result.outcome == "colour_exit"


def test_open_gap_threshold_also_waits_until_following_execution_open():
    raw = raw_bars()
    raw.loc[2, ["open", "high", "low", "close"]] = [121, 122, 120, 121]
    result = replay(raw=raw, takeover_r=2)
    assert result.takeover_trigger_time == START + pd.Timedelta(minutes=10)
    assert not result.takeover_active
    assert result.exit_time == START + pd.Timedelta(minutes=15)


def test_hourly_bar_starting_before_entry_cannot_exit_runner():
    raw = raw_bars(40)
    raw.loc[1, "high"] = 121
    mg60 = management([-1, -1], minutes=60, start=START - pd.Timedelta(minutes=15))
    result = replay(raw=raw, mg60=mg60, takeover_r=2)
    assert result.exit_time == START + pd.Timedelta(minutes=105)


def test_hourly_confirmation_at_takeover_timestamp_is_already_known():
    raw = raw_bars()
    raw.loc[11, "high"] = 121
    result = replay(raw=raw, mg15=management([1] * 8), takeover_r=2)
    assert result.takeover_time == START + pd.Timedelta(minutes=60)
    assert result.exit_time == START + pd.Timedelta(minutes=60)
    assert result.exit_management_minutes == 60


def test_nonhour_takeover_exits_on_latest_already_completed_hour_colour():
    raw = raw_bars()
    raw.loc[15, "high"] = 121
    result = replay(raw=raw, mg15=management([1] * 10), takeover_r=2)
    # The 01:00 hourly bar completed at 02:00 and was already opposite when
    # the 02:15-02:20 execution bar scheduled takeover for the 02:20 open.
    assert result.takeover_time == START + pd.Timedelta(minutes=80)
    assert result.exit_time == START + pd.Timedelta(minutes=80)
    assert result.exit_management_minutes == 60
    assert result.outcome == "colour_exit"


def test_known_hour_reversal_at_takeover_does_not_cancel_same_time_15m_exit():
    raw = raw_bars()
    raw.loc[17, "high"] = 121
    result = replay(raw=raw, mg15=management([1, 1, 1, 1, 1, -1, 1]), takeover_r=2)
    assert result.exit_time == START + pd.Timedelta(minutes=90)
    assert result.exit_management_minutes == 15
    assert pd.isna(result.takeover_time)
    assert not result.takeover_active


@pytest.mark.parametrize("gap_kind", ["missing", "segment"])
def test_partial_profit_survives_gap_but_whole_trade_stays_censored(gap_kind):
    raw = raw_bars()
    raw.loc[0, "high"] = 111
    if gap_kind == "missing":
        raw = raw.drop(index=2).reset_index(drop=True)
    else:
        raw.loc[2:, "segment_id"] = 1
    result = replay(raw=raw, partial_targets=[[1, 0.25]])
    assert result.outcome == "data_gap_censored"
    assert not result.closed
    assert result.partial_fraction == 0.25
    assert result.realised_partial_gross_return == pytest.approx(0.025)
    assert result.realised_partial_net_return == pytest.approx(0.0245)
    assert result.marked_net_return == pytest.approx(0.023)
    assert pd.isna(result.net_return) and pd.isna(result.net_r)
    assert result.exit_time == START + pd.Timedelta(minutes=10)


def test_tail_censor_does_not_promote_realised_partial_to_completed_trade():
    raw = raw_bars(2)
    raw.loc[0, "high"] = 111
    result = replay(raw=raw, partial_targets=[[1, 0.25]])
    assert result.outcome == "right_censored"
    assert not result.closed
    assert result.partial_fraction == 0.25
    assert pd.isna(result.net_return)


def test_valid_open_partial_fill_is_retained_when_later_bar_values_are_missing():
    raw = raw_bars()
    raw.loc[1, ["open", "high", "low", "close"]] = [120, np.nan, np.nan, np.nan]
    result = replay(raw=raw, partial_targets=[[1, 0.25]])
    assert result.outcome == "data_gap_censored" and not result.closed
    assert result.partial_fraction == 0.25
    assert result.realised_partial_gross_return == pytest.approx(0.05)
    assert result.exit_time == START + pd.Timedelta(minutes=5)
    assert result.exit_price == 120
    assert result.marked_net_return == pytest.approx(0.198)
    assert pd.isna(result.net_return)


def test_cutoff_before_bar_completion_hides_high_and_takeover():
    raw = raw_bars()
    raw.loc[0, "high"] = 140
    result = replay(raw=raw, partial_targets=[[1, 0.25]], takeover_r=2,
                    cutoff=START + pd.Timedelta(minutes=2))
    assert result.outcome == "right_censored"
    assert result.partial_fill_count == 0
    assert pd.isna(result.takeover_trigger_time)
    assert result.max_favourable_r == 0


def test_deadline_needs_observable_open_even_after_partial_profit():
    raw = raw_bars(7)
    raw.loc[0, "high"] = 111
    complete = replay(raw=raw, mg15=management([1] * 5), partial_targets=[[1, 0.25]], max_hours=0.5)
    truncated = replay(raw=raw.iloc[:-1], mg15=management([1] * 5), partial_targets=[[1, 0.25]], max_hours=0.5)
    assert complete.outcome == "time_exit" and complete.closed
    assert truncated.outcome == "right_censored" and not truncated.closed


@pytest.mark.parametrize("policy", [
    {"partial_targets": [[1, 0.6], [2, 0.5]]},
    {"partial_targets": [[2, 0.25], [1, 0.25]]},
    {"partial_targets": [[1, -0.25]]},
    {"partial_targets": [[0, 0.25]]},
    {"partial_targets": [[float("nan"), 0.25]]},
    {"takeover_r": 0}, {"takeover_r": float("inf")},
    {"exit_mode": "partial_colour"}, {"confirmations": 2},
])
def test_invalid_policy_is_rejected(policy):
    with pytest.raises(ValueError):
        replay(**policy)


def test_invalid_risk_and_missing_entry_are_not_trades():
    invalid = replay(events=entries(initial_stop=101), partial_targets=[[1, 0.25]])
    missing = replay(events=entries(decision_time=START + pd.Timedelta(minutes=1)), takeover_r=2)
    assert invalid.outcome == "entry_invalid_risk" and not invalid.closed
    assert missing.outcome == "entry_missing" and not missing.closed


def test_future_mutation_after_partial_then_exit_changes_nothing():
    raw = raw_bars()
    raw.loc[0, "high"] = 111
    before = replay(raw=raw, partial_targets=[[1, 0.25]])
    raw.loc[4:, ["open", "high", "low", "close"]] = [1000, 1100, 999, 1050]
    after = replay(raw=raw, partial_targets=[[1, 0.25]])
    for key in ("initial_stop", "entry_price", "exit_time", "exit_price", "net_return",
                "net_r", "max_favourable_r", "max_adverse_r", "realised_partial_gross_return"):
        assert before[key] == after[key]
