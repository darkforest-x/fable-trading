"""Independent synthetic accounting and clock examples; no market-data reads."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.xauusd_execution import simulate


def minute_frame(times, opens, closes=None):
    index = pd.DatetimeIndex(pd.to_datetime(times, utc=True))
    opens = np.asarray(opens, dtype=float)
    closes = opens if closes is None else np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes),
                         "low": np.minimum(opens, closes), "close": closes}, index=index)


def decisions(times):
    closes = pd.DatetimeIndex(pd.to_datetime(times, utc=True))
    # A deliberately unrelated close price must never become a fill.
    return pd.DataFrame({"time_close": closes, "close": 999.0}, index=closes - pd.Timedelta(minutes=1))


def run(minutes, closes, entries, exits_long=None, exits_short=None, start=None, end=None, cost=0):
    bars = decisions(closes)
    n = len(bars)
    return simulate(minutes, bars, np.array(entries), np.zeros(n, bool) if exits_long is None else exits_long,
                    np.zeros(n, bool) if exits_short is None else exits_short,
                    minutes.index[0] if start is None else start,
                    minutes.index[-1] + pd.Timedelta(minutes=1) if end is None else end,
                    cost_bp=cost)


def test_weekend_next_real_open_and_not_signal_close():
    m = minute_frame(["2024-01-05 21:59Z", "2024-01-08 00:00Z", "2024-01-08 00:01Z"], [100, 120, 126])
    result = run(m, ["2024-01-05 22:00Z", "2024-01-08 00:01Z"], [1, 0], [False, True])
    trade = result["ledger"].iloc[0]
    assert trade.entry_time == pd.Timestamp("2024-01-08 00:00Z")
    assert trade.entry_price == 120  # Neither prior Friday close 100 nor decision close 999.
    assert trade.exit_price == 126
    assert result["stats"]["return_pct"] == pytest.approx(5)
    assert result["stats"]["exposure_time"] == 60


def test_fill_at_close_timestamp_uses_new_minutes_open():
    m = minute_frame(pd.date_range("2024-01-01", periods=3, freq="min", tz="UTC"), [100, 110, 120], [105, 112, 121])
    result = run(m, ["2024-01-01 00:01Z", "2024-01-01 00:02Z"], [1, 0], [False, True])
    trade = result["ledger"].iloc[0]
    assert (trade.entry_price, trade.exit_price) == (110, 120)
    assert trade.entry_index == 1
    assert trade.entry_time >= trade.decision_time


def test_compounding_and_fixed_entry_notional_fees():
    m = minute_frame(pd.date_range("2024-01-01", periods=5, freq="min", tz="UTC"), [100, 100, 110, 100, 120])
    result = run(m, m.index[1:], [1, 0, 1, 0], [False, True, False, True], cost=20)
    a, b = result["ledger"].iloc[0], result["ledger"].iloc[1]
    n1 = 1 / 1.001
    e1 = n1 * 1.099
    n2 = e1 / 1.001
    e2 = n2 * 1.199
    assert a.notional == pytest.approx(n1)
    assert a.equity_after == pytest.approx(e1)
    assert b.notional == pytest.approx(n2)
    assert b.equity_after == pytest.approx(e2)
    assert a.entry_fee == pytest.approx(a.exit_fee)
    assert b.entry_fee == pytest.approx(n2 * 0.001)
    assert b.exit_fee == pytest.approx(n2 * 0.001)
    assert list(result["ledger"].net_bp) == pytest.approx([980, 1980])
    assert result["stats"]["return_pct"] == pytest.approx((e2 - 1) * 100)
    assert result["stats"]["winrate"] == 1


def test_exit_precedes_same_decision_reverse_and_no_pyramiding():
    m = minute_frame(pd.date_range("2024-01-01", periods=5, freq="min", tz="UTC"), [100, 100, 110, 110, 99])
    result = run(m, m.index[1:], [1, 1, -1, 0], [False, False, True, False], [False, False, False, True])
    a, b = result["ledger"].iloc[0], result["ledger"].iloc[1]
    assert result["stats"]["trades"] == 2
    assert a.exit_time == b.entry_time == m.index[3]
    assert b.side == -1 and b.units < 0
    assert b.notional == pytest.approx(1.1)
    assert result["stats"]["return_pct"] == pytest.approx(21)


def test_short_profit_and_wrong_side_exit_ignored():
    m = minute_frame(pd.date_range("2024-01-01", periods=4, freq="min", tz="UTC"), [100, 100, 95, 90])
    result = run(m, m.index[1:], [-1, 0, 0], [False, True, False], [False, False, True], cost=20)
    trade = result["ledger"].iloc[0]
    assert trade.side == -1
    assert trade.exit_time == m.index[3]
    assert trade.gross_bp == pytest.approx(1000)
    assert trade.net_bp == pytest.approx(980)


def test_boundary_forced_close_cost_and_excluded_decisions():
    m = minute_frame(pd.date_range("2024-01-01", periods=5, freq="min", tz="UTC"), [100, 100, 110, 999, 999], [100, 101, 115, 999, 999])
    result = run(m, ["2024-01-01 00:00Z", "2024-01-01 00:01Z", "2024-01-01 00:03Z"],
                 [-1, 1, -1], start="2024-01-01 00:01Z", end="2024-01-01 00:03Z", cost=20)
    trade = result["ledger"].iloc[0]
    assert len(result["ledger"]) == 1 and trade.side == 1
    assert trade.exit_reason == "boundary"
    assert trade.exit_time == pd.Timestamp("2024-01-01 00:03Z")
    assert trade.exit_price == 115
    assert trade.exit_index == 2 and pd.isna(trade.exit_decision_time)
    assert trade.net_bp == pytest.approx(1480)
    assert result["daily_equity"].iloc[-1] == pytest.approx(trade.equity_after)


def test_fill_at_or_after_end_is_discarded():
    m = minute_frame(["2024-01-05 21:59Z", "2024-01-08 00:00Z"], [100, 150])
    result = run(m, ["2024-01-05 22:00Z"], [1], end="2024-01-08 00:00Z", cost=20)
    assert result["stats"]["trades"] == 0
    assert result["stats"]["return_pct"] == 0
    assert result["daily_equity"].eq(1).all()


def test_minute_close_drawdown_captures_high_timeframe_round_trip():
    m = minute_frame(pd.date_range("2024-01-01", periods=5, freq="min", tz="UTC"), [100, 100, 120, 50, 100])
    # High-timeframe entry/exit prices are both 100, but minute marks visit 120 and 50.
    result = run(m, [m.index[1], m.index[4]], [1, 0], [False, True])
    assert result["stats"]["return_pct"] == 0
    assert result["stats"]["max_drawdown_pct"] == pytest.approx((1 - 50 / 120) * 100)


def test_bankruptcy_floors_equity_and_stops_later_signals():
    m = minute_frame(pd.date_range("2024-01-01", periods=6, freq="min", tz="UTC"), [100, 100, 250, 90, 90, 200])
    result = run(m, m.index[1:], [-1, 0, 1, 0, 0], cost=20)
    trade = result["ledger"].iloc[0]
    assert result["stats"]["bankrupt"] is True
    assert result["stats"]["return_pct"] == -100
    assert result["stats"]["max_drawdown_pct"] == 100
    assert result["stats"]["trades"] == 1
    assert trade.exit_reason == "bankruptcy"
    assert trade.exit_time == m.index[2] + pd.Timedelta(minutes=1)
    assert trade.equity_after == 0
    assert trade.uncapped_equity_after < 0 and trade.bankruptcy_floor_adjustment > 0
    assert trade.net_pnl == -1
    assert trade.raw_net_bp == pytest.approx(-15020)
    assert result["daily_equity"].iloc[-1] == 0


def test_gap_exit_bankruptcy_prevents_same_decision_reentry():
    m = minute_frame(["2024-01-05 21:58Z", "2024-01-05 21:59Z", "2024-01-08 00:00Z"], [100, 100, 250])
    result = run(m, ["2024-01-05 21:59Z", "2024-01-05 22:00Z"], [-1, 1],
                 [False, False], [False, True], cost=20)
    assert result["stats"]["bankrupt"] is True
    assert result["stats"]["trades"] == 1
    assert result["ledger"].iloc[0].exit_time == pd.Timestamp("2024-01-08 00:00Z")
    assert result["ledger"].iloc[0].exit_price == 250


def test_forced_exit_fee_can_exhaust_remaining_equity():
    m = minute_frame(pd.date_range("2024-01-01", periods=3, freq="min", tz="UTC"), [100, 100, 199.95])
    result = run(m, [m.index[1]], [-1], cost=20)
    # The last mark is positive: (2 - 1.9995) / 1.001. The final exit fee
    # 0.001 / 1.001 is larger and must reduce the last daily mark to zero.
    assert result["stats"]["bankrupt"] is True
    assert result["stats"]["max_drawdown_pct"] == 100
    assert result["daily_equity"].iloc[-1] == 0
    assert result["ledger"].iloc[0].bankruptcy_floor_adjustment > 0


def test_future_signals_do_not_change_earlier_window():
    m = minute_frame(pd.date_range("2024-01-01", periods=7, freq="min", tz="UTC"), [100, 100, 110, 100, 90, 80, 120])
    a = run(m, m.index[1:], [1, 0, 0, 0, 0, 0], [False, True, False, False, False, False], end=m.index[3])
    b = run(m, m.index[1:], [1, 0, -1, 1, -1, 1], [False, True, True, True, True, True], end=m.index[3])
    pd.testing.assert_frame_equal(a["ledger"], b["ledger"])
    pd.testing.assert_series_equal(a["daily_equity"], b["daily_equity"])
    assert a["stats"]["return_pct"] == b["stats"]["return_pct"]


def test_partial_final_minute_is_not_read_past_end():
    m = minute_frame(pd.date_range("2024-01-01", periods=3, freq="min", tz="UTC"), [100, 100, 100], [100, 110, 900])
    result = run(m, [m.index[1]], [1], end="2024-01-01 00:02:30Z")
    assert result["ledger"].iloc[0].exit_price == 110
    assert result["ledger"].iloc[0].exit_time == pd.Timestamp("2024-01-01 00:02Z")


def test_duplicate_minutes_are_rejected():
    m = minute_frame(["2024-01-01 00:00Z", "2024-01-01 00:00Z"], [100, 100])
    with pytest.raises(ValueError, match="unique"):
        run(m, ["2024-01-01 00:01Z"], [1])
