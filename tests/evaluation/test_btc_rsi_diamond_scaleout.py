"""Focused contracts for the no-stop fifth-strong-diamond replay."""
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.evaluation.btc_rsi_diamond_scaleout import (
    replay,
    signals_with_streak,
    trace_position,
)


def _frame(opens, closes=None, lows=None):
    opens = list(map(float, opens))
    closes = opens if closes is None else list(map(float, closes))
    lows = [min(o, c) - 1 for o, c in zip(opens, closes)] if lows is None else list(map(float, lows))
    index = pd.date_range("2025-01-01T00:00Z", periods=len(opens), freq="5min")
    return pd.DataFrame(
        {"open": opens, "high": [max(o, c) + 1 for o, c in zip(opens, closes)],
         "low": lows, "close": closes, "volume": 1.0}, index=index
    )


def _signals(frame, pairs):
    return pd.DataFrame({"strong_side": [side for _, side in pairs]},
                        index=pd.DatetimeIndex([frame.index[i] for i, _ in pairs]))


def _window(frame, end_i=12):
    return frame.index[0], frame.index[end_i]


def test_strong_streak_ignores_zeros_and_only_opposite_strong_resets():
    index = pd.date_range("2025-01-01T00:00Z", periods=7, freq="1h")
    actual = signals_with_streak(pd.DataFrame({"strong_side": [1, 0, 1, 1, -1, 0, -1]}, index=index))
    assert actual.strong_streak.tolist() == [1, 0, 2, 3, 1, 0, 2]


def test_fifth_strong_diamond_enters_at_same_timestamp_raw_open_not_signal_close():
    opens = [100 + i for i in range(24)]
    closes = opens.copy()
    closes[5] = 999.0  # A signal candle's close must never be used as its fill.
    frame = _frame(opens, closes)
    result = replay(frame, _signals(frame, [(i, 1) for i in range(1, 6)]), *_window(frame))
    entry = result["fills"].iloc[0]
    assert entry.fill_type == "entry"
    assert entry.time == frame.index[5]
    assert entry.price == 105.0
    assert entry.price != frame.close.iloc[5]
    assert result["positions"].iloc[0].status == "open"


def test_pre_start_strong_diamonds_warm_the_counter_but_never_carry_a_position():
    frame = _frame([100.0 + i for i in range(24)])
    signals = _signals(frame, [(i, 1) for i in range(1, 6)])
    result = replay(frame, signals, frame.index[5], frame.index[12])
    assert result["fills"].iloc[0].time == frame.index[5]
    assert result["events"].iloc[:4].action.tolist() == ["warmup_count_only"] * 4
    assert result["events"].iloc[4].streak == 5
    assert result["events"].iloc[4].action == "entry"


def test_four_opposite_strong_diamonds_reduce_original_quarters_even_at_losses():
    opens = [100.0] * 24
    opens[6:10] = [90.0, 80.0, 70.0, 60.0]
    frame = _frame(opens)
    signals = _signals(frame, [(i, 1) for i in range(1, 6)] + [(i, -1) for i in range(6, 10)])
    result = replay(frame, signals, *_window(frame))
    exits = result["fills"].query("fill_type == 'partial_exit'")
    position = result["positions"].iloc[0]
    assert exits.fraction.tolist() == [0.25, 0.25, 0.25, 0.25]
    assert exits.price.tolist() == [90.0, 80.0, 70.0, 60.0]
    assert position.status == "closed"
    assert position.remaining_fraction == 0.0
    assert position.gross_realized_return == pytest.approx(-0.25)
    assert position.costs == pytest.approx(0.002)
    assert position.terminal_marked_net_return == pytest.approx(-0.252)
    assert bool(position.noR) is True


def test_no_stop_keeps_deeply_adverse_position_until_an_opposite_strong_diamond():
    opens = [100.0] * 24
    lows = [99.0] * 24
    lows[6] = 1.0
    frame = _frame(opens, lows=lows)
    signals = _signals(frame, [(i, 1) for i in range(1, 6)] + [(6, 1)])
    result = replay(frame, signals, *_window(frame))
    position = result["positions"].iloc[0]
    assert position.status == "open"
    assert position.remaining_fraction == 1.0
    assert result["fills"].fill_type.tolist() == ["entry"]
    assert result["events"].iloc[-1].action == "same_direction_hold"


def test_sixth_or_later_strong_diamond_never_retriggers_or_queues_entry():
    frame = _frame([100.0] * 24)
    result = replay(frame, _signals(frame, [(i, 1) for i in range(1, 7)]), *_window(frame))
    assert result["fills"].fill_type.tolist() == ["entry"]
    assert result["events"].iloc[-1].streak == 6
    assert result["events"].iloc[-1].action == "same_direction_hold"


def test_terminal_mark_subtracts_hypothetical_remaining_exit_cost_separately():
    opens = [100.0] * 24
    closes = [100.0] * 24
    closes[11] = 110.0
    frame = _frame(opens, closes)
    result = replay(frame, _signals(frame, [(i, 1) for i in range(1, 6)]), *_window(frame))
    position = result["positions"].iloc[0]
    assert position.costs == pytest.approx(0.001)
    assert position.terminal_mark_cost == pytest.approx(0.001)
    assert position.terminal_mark_time == frame.index[12]
    assert position.terminal_mark_price == 110.0
    assert position.terminal_marked_net_return == pytest.approx(0.098)


def test_trace_ignores_its_entry_signal_and_uses_later_opposite_strong_events_only():
    opens = [100.0] * 24
    opens[6] = 90.0
    frame = _frame(opens)
    result = trace_position(frame, _signals(frame, [(5, -1), (6, -1)]), frame.index[5], 1, frame.index[12])
    exits = result["fills"].query("fill_type == 'partial_exit'")
    assert exits.time.tolist() == [frame.index[6]]
    assert result["positions"].iloc[0].remaining_fraction == pytest.approx(0.75)


def test_prefix_decisions_and_ledger_do_not_depend_on_future_prices_or_events():
    base = _frame([100.0] * 24)
    changed = base.copy()
    changed.loc[changed.index[12]:, ["open", "high", "low", "close"]] = [999.0, 1000.0, 998.0, 999.0]
    signals = _signals(base, [(i, 1) for i in range(1, 6)])
    first = replay(base, signals, *_window(base))
    second = replay(changed, signals, *_window(changed))
    for name in ("positions", "fills", "events"):
        assert_frame_equal(first[name], second[name])
