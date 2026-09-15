"""Synthetic-only acceptance tests for the strategy-independent ChartArt account."""

from __future__ import annotations

import copy

import pandas as pd
import pytest

from yoyo.evaluation.chartart_martingale_account import simulate_account


def _frame(rows: int = 4) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": [100.] * rows, "high": [101.] * rows, "low": [99.] * rows, "close": [100.] * rows},
        index=pd.date_range("2026-01-01", periods=rows, freq="h", tz="UTC"),
    )


def _trades(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_fee_turns_positive_gross_into_net_loss_and_advances_multiplier() -> None:
    frame = _frame(3)
    first = {"entry_i": 0, "exit_i": 1, "side": 1, "entry_price": 100., "exit_price": 100.1, "censored": False}
    second = {"entry_i": 2, "exit_i": 2, "side": 1, "entry_price": 100., "exit_price": 100., "censored": False}
    summary, sized, _ = simulate_account(frame, _trades([first, second]), multiplier=2)
    assert sized.loc[0, "gross_pnl"] == pytest.approx(.1)
    assert sized.loc[0, "net_pnl"] == pytest.approx(-.1)
    assert sized.loc[1, "executed_multiplier"] == 2
    assert summary["max_consecutive_net_losses"] == 2


def test_same_open_reversal_settles_before_the_new_size_is_admitted() -> None:
    frame = _frame(3)
    frame.iloc[1, :] = [90., 91., 89., 90.]
    trades = _trades([
        {"entry_i": 0, "exit_i": 1, "side": 1, "entry_price": 100., "exit_price": 90., "censored": False},
        {"entry_i": 1, "exit_i": 2, "side": -1, "entry_price": 90., "exit_price": 90., "censored": False},
    ])
    _, sized, _ = simulate_account(frame, trades, multiplier=2, max_leverage=2)
    assert sized.loc[0, "status"] == "closed"
    assert sized.loc[1, "executed_multiplier"] == 2
    assert sized.loc[1, "notional"] == 200


def test_future_prices_cannot_change_already_sized_candidates() -> None:
    frame = _frame(5)
    trades = _trades([
        {"entry_i": 0, "exit_i": 1, "side": 1, "entry_price": 100., "exit_price": 99., "censored": False},
        {"entry_i": 2, "exit_i": 3, "side": 1, "entry_price": 100., "exit_price": 101., "censored": False},
        {"entry_i": 4, "exit_i": 4, "side": 1, "entry_price": 100., "exit_price": 100., "censored": False},
    ])
    changed = frame.copy()
    changed.loc[changed.index[-1], ["open", "high", "low", "close"]] = [400., 500., 1., 300.]
    _, original, _ = simulate_account(frame, trades, multiplier=2, max_leverage=2)
    _, perturbed, _ = simulate_account(changed, copy.deepcopy(trades), multiplier=2, max_leverage=2)
    assert original.loc[:1, ["notional", "executed_multiplier"]].equals(
        perturbed.loc[:1, ["notional", "executed_multiplier"]]
    )


def test_first_winning_trade_can_close_a_still_negative_loss_cycle() -> None:
    frame = _frame(4)
    trades = _trades([
        {"entry_i": 0, "exit_i": 1, "side": 1, "entry_price": 100., "exit_price": 98., "censored": False},
        {"entry_i": 2, "exit_i": 3, "side": 1, "entry_price": 98., "exit_price": 99., "censored": False},
    ])
    summary, sized, _ = simulate_account(frame, trades, multiplier=2, max_leverage=2)
    assert sized.loc[1, "net_pnl"] > 0
    assert summary["negative_closed_cycles"] == 1
    assert summary["cycle_net"] == pytest.approx([-0.5591836734693874])


def test_insufficient_margin_is_terminal_instead_of_skipping_until_affordable() -> None:
    frame = _frame(3)
    trades = _trades([
        {"entry_i": 0, "exit_i": 1, "side": 1, "entry_price": 100., "exit_price": 200., "censored": False},
        {"entry_i": 2, "exit_i": 2, "side": 1, "entry_price": 100., "exit_price": 100., "censored": False},
    ])
    summary, sized, curve = simulate_account(frame, trades, initial_cash=100, base_notional=100, max_leverage=1)
    assert summary["stopped_insufficient_margin"] is True
    assert sized.loc[0, "status"] == "stopped_insufficient_margin"
    assert sized.loc[1, "status"] == "stopped_insufficient_margin"
    assert curve.equity.tolist() == [100., 100., 100.]


def test_intrabar_zero_equity_is_absorbing_even_if_the_close_recovers() -> None:
    frame = _frame(3)
    frame.iloc[0, :] = [100., 150., .1, 150.]
    frame.iloc[1, :] = [150., 151., 149., 150.]
    trades = _trades([
        {"entry_i": 0, "exit_i": 2, "side": 1, "entry_price": 100., "exit_price": 150., "censored": True},
    ])
    summary, sized, curve = simulate_account(frame, trades, initial_cash=100, base_notional=100, max_leverage=2)
    assert summary["stopped_zero_equity"] is True and summary["ending_equity"] == 0
    assert sized.loc[0, "status"] == "stopped_zero_equity"
    assert curve.equity.tolist() == [0., 0., 0.]


def test_natural_open_exit_is_not_exposed_to_that_bars_later_wick() -> None:
    frame = _frame(3)
    frame.iloc[1, :] = [100., 150., .1, 150.]
    trades = _trades([
        {"entry_i": 0, "exit_i": 1, "side": 1, "entry_price": 100., "exit_price": 100., "censored": False},
    ])
    summary, sized, _ = simulate_account(frame, trades, initial_cash=100, base_notional=100, max_leverage=2)
    assert sized.loc[0, "status"] == "closed"
    assert summary["stopped_zero_equity"] is False
    assert summary["ending_equity"] == pytest.approx(99.8)


def test_censored_tail_is_marked_at_final_close_but_never_updates_realized_stats() -> None:
    frame = _frame(2)
    frame.iloc[1, :] = [100., 111., 99., 110.]
    trades = _trades([
        {"entry_i": 0, "exit_i": 1, "side": 1, "entry_price": 100., "exit_price": 999., "censored": True},
    ])
    summary, sized, curve = simulate_account(frame, trades, max_leverage=2)
    assert sized.loc[0, "status"] == "censored" and sized.loc[0, "exit_price_used"] == 110
    assert sized.loc[0, "net_pnl"] == pytest.approx(9.8)
    assert summary["closed_trades"] == summary["closed_wins"] == 0
    assert summary["max_consecutive_net_losses"] == 0
    assert curve.iloc[-1].equity == pytest.approx(1009.8)


def test_curve_omits_flat_warmup_but_preserves_source_bar_offsets() -> None:
    frame = _frame(5)
    trades = _trades([
        {"entry_i": 3, "exit_i": 4, "side": 1, "entry_price": 100., "exit_price": 100., "censored": False},
    ])
    _, _, curve = simulate_account(frame, trades)
    assert curve.bar_i.tolist() == [3, 4]
    assert curve.index.tolist() == frame.index[3:].tolist()
