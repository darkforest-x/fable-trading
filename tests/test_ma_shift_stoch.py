"""Focused causality and ledger contracts for the offline MA/Stoch engine."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import yoyo.evaluation.ma_shift_stoch as engine
from yoyo.evaluation.spike_fanshen_exit import compute_signals


def bars(n: int = 150) -> pd.DataFrame:
    index = pd.date_range("2026-08-01", periods=n, freq="5min", tz="UTC")
    base = 100 + np.arange(n, dtype=float) * .1
    return pd.DataFrame({"open": base, "high": base + 1, "low": base - 1,
                         "close": base + .2, "volume": np.ones(n)}, index=index)


def arrows_at(monkeypatch: pytest.MonkeyPatch, entries: dict[int, int]) -> None:
    def fake(frame: pd.DataFrame) -> pd.DataFrame:
        result = pd.DataFrame(False, index=frame.index,
                              columns=["arrow_long", "arrow_short"])
        for i, side in entries.items():
            result.iloc[i, 0 if side == 1 else 1] = True
        return result
    monkeypatch.setattr(engine, "compute_signals", fake)


def test_arrows_are_the_existing_strict_stoch_arrows() -> None:
    frame = bars()
    actual = engine.build_signals(frame)[["arrow_long", "arrow_short"]]
    expected = compute_signals(frame)[["arrow_long", "arrow_short"]]
    pd.testing.assert_frame_equal(actual, expected)


def test_completed_15m_clock_and_future_perturbation_do_not_change_prefix() -> None:
    frame = bars(150)
    before = engine.build_signals(frame)
    ready = before["direction_15m_close_time"].notna()
    assert ready.any()
    assert (before.loc[ready, "direction_15m_close_time"] <= before.loc[ready, "close_time"]).all()
    first = before.index[ready.argmax()]
    assert pd.isna(before.loc[first - pd.Timedelta(minutes=5), "direction_15m"])

    changed = frame.copy()
    changed.loc[changed.index[132]:, "high"] += 100
    changed.loc[changed.index[132]:, "low"] += 100
    changed.loc[changed.index[132]:, ["open", "close"]] += 100
    after = engine.build_signals(changed)
    pd.testing.assert_frame_equal(before.iloc[:132], after.iloc[:132])


def test_next_open_opposite_exit_ignores_ma_and_can_flip(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = bars(150)
    arrows_at(monkeypatch, {121: 1, 124: -1, 127: 1})
    result = engine.run_backtest(frame, frame.index[121] + pd.Timedelta(minutes=5),
                                 frame.index[130], use_ma_filter=False)
    assert result.trades.trade_id.tolist() == [1, 2]
    first, second = result.trades.iloc[0], result.trades.iloc[1]
    assert first.entry_time == frame.index[122] and first.exit_time == frame.index[125]
    assert second.entry_time == frame.index[125] and second.exit_time == frame.index[128]
    assert first.side == 1 and second.side == -1
    at_flip = result.fills.loc[result.fills.time.eq(frame.index[125])]
    assert at_flip.fill_type.tolist() == ["exit", "entry"]
    assert result.open_positions.side.tolist() == [1]


def test_opposite_arrow_closes_even_when_ma_blocks_reverse(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = bars(150)
    arrows_at(monkeypatch, {121: 1, 124: -1})
    result = engine.run_backtest(frame, frame.index[121] + pd.Timedelta(minutes=5),
                                 frame.index[130], use_ma_filter=True)
    assert len(result.trades) == 1
    assert result.trades.iloc[0].side == 1
    assert result.trades.iloc[0].exit_time == frame.index[125]
    assert result.open_positions.empty


def test_boundary_excludes_execution_at_end_and_does_not_make_synthetic_trade(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = bars(150)
    arrows_at(monkeypatch, {121: 1, 129: -1})
    start, end = frame.index[121] + pd.Timedelta(minutes=5), frame.index[130]
    result = engine.run_backtest(frame, start, end, use_ma_filter=False)
    assert len(result.fills) == 1 and result.fills.iloc[0].time == frame.index[122]
    assert result.trades.empty
    assert len(result.open_positions) == 1
    assert result.open_positions.iloc[0].marked_time == end
    assert result.equity_curve.iloc[-1].event == "close_mtm"


def test_half_exit_keeps_position_through_same_side_arrow_and_cost_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = bars(150)
    arrows_at(monkeypatch, {121: 1, 124: -1, 126: 1, 128: -1})
    result = engine.run_backtest(frame, frame.index[121] + pd.Timedelta(minutes=5), frame.index[135],
                                 use_ma_filter=False, exit_mode="two_opposite_half")
    assert len(result.trades) == 1
    trade = result.trades.iloc[0]
    exits = result.fills.loc[result.fills.fill_type.eq("exit")]
    assert exits.fraction.tolist() == [.5, .5]
    assert exits.time.tolist() == [frame.index[125], frame.index[129]]
    first_trade_fills = result.fills.loc[result.fills.trade_id.eq(trade.trade_id)]
    assert first_trade_fills.cost_return.sum() == pytest.approx(.002)
    assert trade.net_return == pytest.approx(trade.gross_return - .002)


def test_replay_one_forces_entry_without_arrow_or_ma_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = bars(150)
    arrows_at(monkeypatch, {125: -1})
    signals = engine.build_signals(frame)
    replay = engine.replay_one(frame, signals, 121, 1, "full_opposite", frame.index[130])
    assert replay.trade.iloc[0].entry_time == frame.index[122]
    assert replay.trade.iloc[0].exit_time == frame.index[126]
    assert replay.trade.iloc[0].side == 1
    assert replay.open_position.empty


def test_rejects_noncontinuous_ohlcv() -> None:
    frame = bars().drop(bars().index[10])
    with pytest.raises(ValueError, match="continuous"):
        engine.run_backtest(frame, frame.index[0], frame.index[-1] + pd.Timedelta(minutes=5))
