"""Synthetic causal tests for the V12.8 chart-reference hint replay."""
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v128_hint_replay import replay_reference_and_hints
from yoyo.evaluation.spike_v10_4 import reference_long_exits


def _market(periods=180, *, start="2025-01-01T00:00:00Z"):
    index = pd.date_range(start, periods=periods, freq="5min", tz="UTC")
    close = np.full(periods, 100.0)
    bars = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close}, index=index)
    return bars


def _chart(base, minutes=60):
    grouped = base.groupby(base.index.floor(f"{minutes}min"))
    out = grouped.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    out["atr"] = 1.0
    return out


def _facts(frame, entry_i=4):
    n = len(frame)
    side = np.zeros(n, dtype=int); v9 = np.zeros(n, dtype=bool)
    side[entry_i] = 1; v9[entry_i] = True
    return {"frame": frame, "side": side, "v9": v9, "ready": np.ones(n, bool), "gap": np.zeros(n, bool)}


def _set_hour(base, hour, values):
    ix = base.index.floor("h") == pd.Timestamp(hour, tz="UTC")
    values = list(values)
    base.loc[ix, "close"] = values[-1]
    base.loc[ix, "open"] = values[0]
    base.loc[ix, "high"] = max(values) + 1
    base.loc[ix, "low"] = min(values) - 1


def _pattern(base):
    # Entry at 05:00 chart close.  Intermediate chart closes deliberately stay
    # above the inherited protection: this tests V128 state, not a trail stop.
    _set_hour(base, "2025-01-01 05:00", [102, 102])
    base.loc[base.index.floor("h") == pd.Timestamp("2025-01-01 05:00", tz="UTC"), "high"] = 111
    _set_hour(base, "2025-01-01 06:00", [101, 101])
    _set_hour(base, "2025-01-01 07:00", [121, 121])
    _set_hour(base, "2025-01-01 08:00", [122, 122])
    base.loc[base.index.floor("h") == pd.Timestamp("2025-01-01 08:00", tz="UTC"), "high"] = 131
    _set_hour(base, "2025-01-01 09:00", [119, 119])
    base.loc[base.index.floor("h") == pd.Timestamp("2025-01-01 09:00", tz="UTC"), "low"] = 119
    _set_hour(base, "2025-01-01 10:00", [141, 141])
    _set_hour(base, "2025-01-01 11:00", [142, 142])
    base.loc[base.index.floor("h") == pd.Timestamp("2025-01-01 11:00", tz="UTC"), "high"] = 151
    _set_hour(base, "2025-01-01 12:00", [139, 139])
    base.loc[base.index.floor("h") == pd.Timestamp("2025-01-01 12:00", tz="UTC"), "low"] = 139
    _set_hour(base, "2025-01-01 13:00", [161, 161])
    return base


def test_native_probe_normal_cap_and_reference_updates_after_cap():
    base = _pattern(_market(15 * 12))
    chart = _chart(base, 60)
    facts = _facts(chart, 4)  # entry close is 05:00
    frames, hints = replay_reference_and_hints(facts, base, .01, 60, chart.index[0], chart.index[-1] + pd.Timedelta(hours=1))
    assert hints.ordinal.tolist() == [1, 2]
    assert hints.price.tolist() == [121.0, 141.0]
    assert hints.reference_price.tolist() == [99.99, 118.99]
    assert frames.iloc[0].hint_count == 2
    assert frames.iloc[0].structural_update_count == 3  # cap suppresses only the third candidate.
    assert frames.iloc[0].last_structural_reference == 138.99


def test_h1_gap_fails_closed_after_first_candidate():
    whole = _pattern(_market(15 * 12))
    base = whole.drop(pd.date_range("2025-01-01 09:00", periods=12, freq="5min", tz="UTC"))
    chart = _chart(whole, 60)  # chart bars still exist; only the requested H1 is incomplete.
    facts = _facts(chart, 4)
    _, hints = replay_reference_and_hints(facts, base, .01, 60, chart.index[0], chart.index[-1] + pd.Timedelta(hours=1))
    assert hints.ordinal.tolist() == [1]


def test_15m_observes_completed_h1_at_bar_open_then_marks_chart_close():
    base = _pattern(_market(15 * 12))
    chart = _chart(base, 15)
    facts = _facts(chart, 19)  # 04:45 bar closes at entry 05:00
    _, hints = replay_reference_and_hints(facts, base, .01, 15, chart.index[0], chart.index[-1] + pd.Timedelta(minutes=15))
    first = hints.iloc[0]
    assert first.h1_close_time == pd.Timestamp("2025-01-01 08:00:00Z")
    assert first.signal_close == pd.Timestamp("2025-01-01 08:15:00Z")


def test_prefix_replay_does_not_use_future_and_keeps_prior_frame_for_window_hint():
    base = _pattern(_market(15 * 12))
    chart = _chart(base, 15); facts = _facts(chart, 19)
    start, end = pd.Timestamp("2025-01-01 08:00Z"), pd.Timestamp("2025-01-01 09:00Z")
    whole_frames, whole_hints = replay_reference_and_hints(facts, base, .01, 15, start, end)
    cut = chart.index.get_loc(pd.Timestamp("2025-01-01 08:00Z")) + 1
    prefix_facts = dict(facts, frame=chart.iloc[:cut]); prefix_facts["side"] = facts["side"][:cut]; prefix_facts["v9"] = facts["v9"][:cut]; prefix_facts["ready"] = facts["ready"][:cut]; prefix_facts["gap"] = facts["gap"][:cut]
    _, prefix_hints = replay_reference_and_hints(prefix_facts, base.loc[:"2025-01-01 08:00Z"], .01, 15, start, end)
    assert whole_frames.empty  # entry was before window, hint must retain its frame key.
    assert len(whole_hints) == len(prefix_hints) == 1
    assert whole_hints.iloc[0].frame_key == prefix_hints.iloc[0].frame_key


def test_stop_bar_exits_before_hint_state_and_reverse_ends_reference():
    base = _market(15 * 12)
    chart = _chart(base, 60); facts = _facts(chart, 4)
    chart.loc[chart.index[6], "low"] = 90.0
    frames, hints = replay_reference_and_hints(facts, base, .01, 60, chart.index[0], chart.index[-1] + pd.Timedelta(hours=1))
    assert hints.empty
    assert frames.iloc[0].exit_reason == "protection_touch"
    assert frames.iloc[0].exit_i == 6


def test_chart_gap_censors_reference_without_using_post_gap_close_as_an_exit():
    base = _market(15 * 12)
    chart = _chart(base, 60); facts = _facts(chart, 4)
    facts["gap"][6] = True
    frames, _ = replay_reference_and_hints(facts, base, .01, 60, chart.index[0], chart.index[-1] + pd.Timedelta(hours=1))
    row = frames.iloc[0]
    assert row.exit_reason == "data_gap" and row.censored
    assert np.isnan(row.gross_r) and np.isnan(row.net_r)


def test_reference_frame_long_exit_matches_existing_v104_parent_on_raw_reverse():
    base = _market(15 * 12)
    chart = _chart(base, 60); facts = _facts(chart, 4)
    facts["side"][10] = -1  # raw side reverses a parent even though V9 rejects the short.
    legacy = reference_long_exits(chart.high, chart.low, chart.close, chart.atr,
                                  ready=facts["ready"], gap=facts["gap"], raw_side=facts["side"],
                                  signal_side=np.where(facts["v9"], facts["side"], 0), tick=.01)
    frames, _ = replay_reference_and_hints(facts, base, .01, 60, chart.index[0], chart.index[-1] + pd.Timedelta(hours=1))
    assert np.flatnonzero(legacy).tolist() == [10]
    assert frames.iloc[0].exit_i == 10 and frames.iloc[0].exit_reason == "reverse_confirmation"
