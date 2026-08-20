"""Causal and accounting checks for the ALLIN-V7 research translation."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l3_backtest.pine_allin_v7 import (
    Arm,
    ExecutionParameters,
    SignalParameters,
    add_indicators,
    auc_from_scores,
    deterministic_control_indices,
    in_hour_window,
    load_development_frame,
    pine_rma,
    simulate_symbol,
)


def test_pine_rma_uses_sma_seed_then_wilder_update() -> None:
    result = pine_rma([1.0, 2.0, 3.0, 4.0], 3)
    assert np.isnan(result[0]) and np.isnan(result[1])
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx((2.0 * 2.0 + 4.0) / 3.0)


def test_hour_window_supports_regular_overnight_and_equal_boundaries() -> None:
    hours = np.array([0, 2, 3, 20, 21, 22, 23])
    assert in_hour_window(hours, 21, 23).tolist() == [False, False, False, False, True, True, False]
    assert in_hour_window(hours, 23, 3).tolist() == [True, True, False, False, False, False, True]
    assert not in_hour_window(hours, 5, 5).any()


def test_loader_refuses_an_end_without_a_full_chunk_holdout_buffer(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    path.write_text(
        "open_time,open,high,low,close,volume\n"
        "2026-01-01T00:00:00Z,1,1,1,1,1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="parser chunk"):
        load_development_frame(
            path,
            safe_end=pd.Timestamp("2026-05-03T00:00:00Z"),
            holdout_start=pd.Timestamp("2026-05-04T00:00:00Z"),
            chunksize=100,
        )


def _frame_for_stop() -> pd.DataFrame:
    times = pd.date_range("2025-01-01", periods=260, freq="15min", tz="UTC")
    close = np.linspace(100.0, 102.0, len(times))
    frame = pd.DataFrame(
        {
            "open_time": times,
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 1.0,
        }
    )
    result = add_indicators(frame, SignalParameters())
    result[["v7_long", "v7_short"]] = False
    result.loc[240, "v7_long"] = True
    result.loc[240, "entry_allowed"] = True
    result.loc[240, "atr"] = 1.0
    result.loc[240, "v7_score"] = 1.0
    # Entry at bar 241, then the same bar breaches the protected stop.
    result.loc[241, "open"] = 100.0
    result.loc[241, "low"] = 96.0
    result.loc[241, "close"] = 99.0
    return result


def test_initial_stop_is_active_on_the_entry_bar_and_cost_is_round_trip() -> None:
    frame = _frame_for_stop()
    trades, _ = simulate_symbol(
        frame,
        symbol="TEST_USDT_SWAP",
        arm=Arm(name="risk", signal_kind="v7", sizing_kind="risk"),
        start=pd.Timestamp("2025-01-01T00:00:00Z"),
        end=pd.Timestamp("2025-01-04T00:00:00Z"),
        params=SignalParameters(),
        round_trip_cost=0.002,
    )
    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["entry_i"] == 241
    assert trade["exit_i"] == 241
    assert trade["exit_reason"] == "stop"
    assert trade["gross_return"] == pytest.approx(-0.03)
    assert trade["net_return"] == pytest.approx(-0.032)
    assert trade["leverage"] == pytest.approx(2.0 / 3.0)


def test_pine_v8_execution_freezes_signal_close_ticks_quantity_and_fill_fees() -> None:
    frame = _frame_for_stop()
    frame.loc[240, "close"] = 100.13
    frame.loc[240, "atr"] = 0.251  # 4*ATR=1.004 -> 100 ticks -> 1.00
    frame.loc[241, "open"] = 101.0
    frame.loc[241, "low"] = 99.0
    trades, _ = simulate_symbol(
        frame,
        symbol="TEST_USDT_SWAP",
        arm=Arm(name="risk", signal_kind="v7", sizing_kind="risk"),
        start=pd.Timestamp("2025-01-01T00:00:00Z"),
        end=pd.Timestamp("2025-01-04T00:00:00Z"),
        params=SignalParameters(),
        round_trip_cost=0.002,
        execution=ExecutionParameters(
            stop_distance_basis="signal_close",
            sizing_price_basis="signal_close",
            tick_size=0.01,
            commission_per_side=0.001,
            skip_return_basis="net",
        ),
    )
    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["initial_stop_distance"] == pytest.approx(1.0)
    assert trade["exit_price"] == pytest.approx(100.0)
    # Pine quantity is 10 contracts from the signal close; the gap makes actual
    # entry leverage 2.02x rather than the 2.0026x signal-time target.
    assert trade["leverage"] == pytest.approx(2.02)
    gross = 100.0 / 101.0 - 1.0
    assert trade["gross_return"] == pytest.approx(gross)
    assert trade["project_net_return"] == pytest.approx(gross - 0.002)
    assert trade["commission_return"] == pytest.approx(0.001 * (1.0 + 100.0 / 101.0))
    assert trade["net_return"] == pytest.approx(
        gross - 0.001 * (1.0 + 100.0 / 101.0)
    )
    assert trade["quantity"] == pytest.approx(10.0)
    assert trade["initial_stop_price"] == pytest.approx(100.0)


def test_break_even_can_be_disabled_without_changing_the_signal() -> None:
    frame = _frame_for_stop()
    frame.loc[241, ["open", "high", "low", "close"]] = [100.0, 102.0, 99.5, 101.0]
    frame.loc[242, ["open", "high", "low", "close"]] = [101.0, 101.2, 100.0, 100.5]
    common = dict(
        frame=frame,
        symbol="TEST_USDT_SWAP",
        start=pd.Timestamp("2025-01-01T00:00:00Z"),
        end=pd.Timestamp("2025-01-04T00:00:00Z"),
        params=SignalParameters(),
        round_trip_cost=0.002,
    )
    with_be, _ = simulate_symbol(
        arm=Arm(name="with_be", signal_kind="v7", sizing_kind="risk", use_break_even=True),
        **common,
    )
    without_be, _ = simulate_symbol(
        arm=Arm(name="without_be", signal_kind="v7", sizing_kind="risk", use_break_even=False),
        **common,
    )
    assert with_be.iloc[0]["exit_reason"] == "stop"
    assert with_be.iloc[0]["exit_price"] == pytest.approx(100.1)
    assert without_be.iloc[0]["exit_reason"] == "period_end"


def test_equity_frequency_none_keeps_each_15m_mark() -> None:
    frame = _frame_for_stop()
    _, marked = simulate_symbol(
        frame,
        symbol="TEST_USDT_SWAP",
        arm=Arm(name="bar_equity", signal_kind="v7", sizing_kind="risk"),
        start=pd.Timestamp("2025-01-01T00:00:00Z"),
        end=pd.Timestamp("2025-01-04T00:00:00Z"),
        params=SignalParameters(),
        round_trip_cost=0.002,
        execution=ExecutionParameters(equity_frequency=None),
    )
    unique_times = marked["open_time"].drop_duplicates().sort_values()
    assert len(unique_times) == len(frame)
    assert unique_times.diff().dropna().min() == pd.Timedelta(minutes=15)


def test_close_only_opposite_signal_does_not_open_the_reverse_side() -> None:
    frame = _frame_for_stop()
    frame.loc[241, ["open", "high", "low", "close"]] = [100.0, 100.2, 99.8, 100.0]
    frame.loc[242, "v7_short"] = True
    frame.loc[242, "entry_allowed"] = True
    frame.loc[242, "atr"] = 1.0
    frame.loc[242, "v7_score"] = 1.0
    common = dict(
        frame=frame,
        symbol="TEST_USDT_SWAP",
        start=pd.Timestamp("2025-01-01T00:00:00Z"),
        end=pd.Timestamp("2025-01-04T00:00:00Z"),
        params=SignalParameters(),
        round_trip_cost=0.002,
    )
    reversed_trades, _ = simulate_symbol(
        arm=Arm(
            name="reverse",
            signal_kind="v7",
            sizing_kind="risk",
            opposite_signal_action="reverse",
        ),
        **common,
    )
    close_only_trades, _ = simulate_symbol(
        arm=Arm(
            name="close_only",
            signal_kind="v7",
            sizing_kind="risk",
            opposite_signal_action="close_only",
        ),
        **common,
    )
    assert reversed_trades["direction"].tolist() == ["long", "short"]
    assert close_only_trades["direction"].tolist() == ["long"]
    assert close_only_trades.iloc[0]["exit_reason"] == "reverse"


def test_entry_direction_gate_keeps_opposite_signal_as_an_exit() -> None:
    frame = _frame_for_stop()
    frame.loc[241, ["open", "high", "low", "close"]] = [100.0, 100.2, 99.8, 100.0]
    frame.loc[242, "v7_short"] = True
    frame.loc[242, "entry_allowed"] = True
    frame.loc[242, "atr"] = 1.0
    frame.loc[242, "v7_score"] = 1.0
    trades, _ = simulate_symbol(
        frame,
        symbol="TEST_USDT_SWAP",
        arm=Arm(
            name="long_only",
            signal_kind="v7",
            sizing_kind="risk",
            entry_directions=(1,),
        ),
        start=pd.Timestamp("2025-01-01T00:00:00Z"),
        end=pd.Timestamp("2025-01-04T00:00:00Z"),
        params=SignalParameters(),
        round_trip_cost=0.002,
    )
    assert trades["direction"].tolist() == ["long"]
    assert trades.iloc[0]["exit_reason"] == "reverse"
    assert trades.iloc[0]["exit_i"] == 243


def test_deterministic_controls_do_not_depend_on_input_order() -> None:
    a = deterministic_control_indices("candidate", [9, 2, 5, 1], n=3, seed="s")
    b = deterministic_control_indices("candidate", [1, 5, 2, 9], n=3, seed="s")
    assert a == b
    assert len(a) == 3 and len(set(a)) == 3


def test_auc_is_tie_aware() -> None:
    assert auc_from_scores([0.1, 0.2, 0.3, 0.4], [False, False, True, True]) == pytest.approx(1.0)
    assert auc_from_scores([1.0, 1.0], [False, True]) == pytest.approx(0.5)
