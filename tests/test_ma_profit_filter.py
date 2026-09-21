import pandas as pd

from yoyo.contracts.ma_profit_filter import resolve_ma_profit_event


def frame(rows, minutes=15):
    data = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    data.insert(0, "open_time", pd.date_range("2026-01-01T00:00:00Z", periods=len(data), freq=f"{minutes}min"))
    return data


def event(rows, direction="LONG", **kwargs):
    return resolve_ma_profit_event(frame(rows), 0, 0, direction, confirmation_bars=1, horizon_hours=1, **kwargs)


def seed(n=8): return [[100, 101, 99, 100]] + [[100, 100, 100, 100] for _ in range(n - 1)]


def test_long_tp_short_tp_and_linear_short_symmetry():
    a = seed(); a[2] = [100, 104, 100, 103]
    assert event(a, target_r=3)["outcome"] == "TP"
    b = [[100, 101, 99, 100] for _ in range(8)]; b[2] = [100, 100, 96, 97]
    assert event(b, "SHORT", target_r=3)["outcome"] == "TP"


def test_sl_wins_same_bar_and_gap_sl_can_exceed_one_r():
    a = seed(); a[2] = [100, 104, 98, 103]
    assert event(a, target_r=3)["outcome"] == "SL"
    b = seed(); b[3] = [97, 98, 96, 97]
    result = event(b, target_r=3)
    assert result["reason"] == "sl_gap_open" and result["gross_r"] < -1


def test_entry_already_through_core_stop_is_invalid_not_an_sl_label():
    a = seed(); a[2] = [97, 98, 96, 97]
    assert event(a)["reason"] == "stop_not_beyond_entry"


def test_earlier_sl_closes_even_if_a_later_bar_reaches_tp():
    a = seed(); a[2] = [100, 101, 98, 99]; a[3] = [100, 104, 100, 103]
    assert event(a, target_r=3)["outcome"] == "SL"


def test_tp_gap_is_filled_at_target_and_fee_can_reject_retention():
    a = seed(); a[3] = [105, 106, 104, 105]
    result = event(a, target_r=3, round_trip_cost=.04)
    assert result["reason"] == "tp_gap_target" and result["gross_r"] == 3 and not result["retained"]
    assert result["exit_time_utc"] == frame(a)["open_time"].iloc[3].isoformat()


def test_timeout_censor_gap_and_nonfinite_are_not_false_labels():
    assert event(seed(), target_r=30)["outcome"] == "TIMEOUT"
    assert event(seed(3), target_r=30)["outcome"] == "UNKNOWN"
    gapped = frame(seed()); gapped.loc[3, "open_time"] += pd.Timedelta(minutes=15)
    assert resolve_ma_profit_event(gapped, 0, 0, "LONG", confirmation_bars=1, horizon_hours=1)["outcome"] == "UNKNOWN"
    bad = seed(); bad[2][2] = float("nan")
    assert event(bad)["outcome"] == "INVALID"


def test_signal_bar_is_not_entry_bar_profit():
    rows = seed(); rows[1] = [100, 105, 99, 104]; rows[2] = [100, 101, 99, 100]
    assert event(rows, target_r=3)["outcome"] != "TP"


def test_nonpositive_short_target_is_invalid():
    assert event(seed(), "SHORT", target_r=200)["reason"] == "nonpositive_short_target"


def test_iloc_indexing_and_invalid_future_candle_are_rejected():
    indexed = frame(seed()); indexed.index = range(100, 100 + len(indexed))
    assert resolve_ma_profit_event(indexed, 0, 0, "LONG", confirmation_bars=1, horizon_hours=1, target_r=30)["outcome"] == "TIMEOUT"
    invalid = seed(); invalid[2] = [100, 99, 100, 100]
    assert event(invalid)["reason"] == "invalid_preentry_ohlc"
