"""Synthetic acceptance tests for shared SPIKE account accounting only."""
from __future__ import annotations

import pandas as pd
import pytest

from yoyo.evaluation.spike_account_growth import simulate_shared_account


def _trades(rows):
    frame = pd.DataFrame(rows)
    for name in ("entry_time", "exit_time"):
        frame[name] = pd.to_datetime(frame[name], utc=True)
    return frame


def _run(rows, **kwargs):
    return simulate_shared_account(_trades(rows), sizing="compound", risk_fraction=.10, **kwargs)


def test_compound_and_fixed_reinvest_different_realized_balances():
    rows = [
        dict(trade_id="a", entry_time="2024-01-01T00:00Z", exit_time="2024-01-01T01:00Z", base_asset="A", entry_price=100, initial_risk=10, side=1, net_return=.20),
        dict(trade_id="b", entry_time="2024-01-01T01:00Z", exit_time="2024-01-01T02:00Z", base_asset="B", entry_price=100, initial_risk=10, side=1, net_return=.20),
    ]
    compound = _run(rows)
    fixed = simulate_shared_account(_trades(rows), sizing="fixed", risk_fraction=.10)
    assert compound["summary"]["final_balance"] == pytest.approx(1440)
    assert fixed["summary"]["final_balance"] == pytest.approx(1400)
    assert compound["ledger"].set_index("trade_id").loc["b", "quantity"] == pytest.approx(12)
    assert fixed["ledger"].set_index("trade_id").loc["b", "quantity"] == pytest.approx(10)


def test_portfolio_risk_leverage_and_one_asset_limits_are_all_recorded():
    rows = [
        dict(trade_id="a", entry_time="2024-01-01T00:00Z", exit_time="2024-01-01T10:00Z", base_asset="A", entry_price=100, initial_risk=5, side=1, net_return=0),
        dict(trade_id="same-a", entry_time="2024-01-01T01:00Z", exit_time="2024-01-01T10:00Z", base_asset="A", entry_price=100, initial_risk=5, side=1, net_return=0),
        dict(trade_id="lever", entry_time="2024-01-01T02:00Z", exit_time="2024-01-01T10:00Z", base_asset="B", entry_price=100, initial_risk=1, side=1, net_return=0),
        dict(trade_id="c", entry_time="2024-01-01T03:00Z", exit_time="2024-01-01T10:00Z", base_asset="C", entry_price=100, initial_risk=5, side=1, net_return=0),
        dict(trade_id="d", entry_time="2024-01-01T04:00Z", exit_time="2024-01-01T10:00Z", base_asset="D", entry_price=100, initial_risk=5, side=1, net_return=0),
        dict(trade_id="risk", entry_time="2024-01-01T05:00Z", exit_time="2024-01-01T10:00Z", base_asset="E", entry_price=100, initial_risk=5, side=1, net_return=0),
    ]
    result = simulate_shared_account(_trades(rows), sizing="fixed", risk_fraction=.03)
    ledger = result["ledger"].set_index("trade_id")
    assert ledger.loc["a", "selected"]
    assert ledger.loc["same-a", "rejection_reason"] == "base_asset_open"
    assert ledger.loc["lever", "rejection_reason"] == "gross_leverage_cap"
    assert ledger.loc["risk", "rejection_reason"] == "portfolio_risk_cap"
    assert result["summary"]["max_gross_leverage"] <= 3
    assert result["summary"]["max_portfolio_initial_risk"] <= .10


def test_exit_precedes_same_time_entry_and_releases_capacity():
    rows = [
        dict(trade_id="a", entry_time="2024-01-01T00:00Z", exit_time="2024-01-01T01:00Z", base_asset="A", entry_price=100, initial_risk=10, side=1, net_return=.10),
        dict(trade_id="b", entry_time="2024-01-01T01:00Z", exit_time="2024-01-01T02:00Z", base_asset="B", entry_price=100, initial_risk=10, side=1, net_return=0),
    ]
    ledger = _run(rows)["ledger"].set_index("trade_id")
    assert ledger.loc["b", "selected"]
    assert ledger.loc["b", "entry_balance"] == pytest.approx(1100)
    assert ledger.loc["b", "quantity"] == pytest.approx(11)


def test_same_bar_entry_and_exit_settle_after_its_entry_group():
    rows = [
        dict(trade_id="instant", entry_time="2024-01-01T00:00Z", exit_time="2024-01-01T00:00Z", base_asset="A", entry_price=100, initial_risk=10, side=1, net_return=.10),
        dict(trade_id="later", entry_time="2024-01-01T01:00Z", exit_time="2024-01-01T02:00Z", base_asset="B", entry_price=100, initial_risk=10, side=1, net_return=0),
    ]
    ledger = _run(rows)["ledger"].set_index("trade_id")
    assert ledger.loc["instant", "selected"]
    assert ledger.loc["instant", "realized_pnl"] == pytest.approx(100)
    assert ledger.loc["later", "entry_balance"] == pytest.approx(1100)


def test_gap_loss_can_exceed_one_r_and_floor_latches_new_entries():
    rows = [
        dict(trade_id="gap", entry_time="2024-01-01T00:00Z", exit_time="2024-01-01T01:00Z", base_asset="A", entry_price=100, initial_risk=10, side=1, net_return=-.85),
        dict(trade_id="after-floor", entry_time="2024-01-01T01:00Z", exit_time="2024-01-01T02:00Z", base_asset="B", entry_price=100, initial_risk=10, side=1, net_return=.20),
    ]
    result = _run(rows)
    ledger = result["ledger"].set_index("trade_id")
    assert ledger.loc["gap", "realized_pnl"] == pytest.approx(-850)
    assert ledger.loc["gap", "exit_r_multiple"] == pytest.approx(-8.5)
    assert result["summary"]["final_balance"] == pytest.approx(150)
    assert result["summary"]["floor_triggered"]
    assert ledger.loc["after-floor", "rejection_reason"] == "entry_floor"


def test_bankruptcy_is_floored_at_zero_and_remains_closed_to_entries():
    rows = [
        dict(trade_id="wipeout", entry_time="2024-01-01T00:00Z", exit_time="2024-01-01T01:00Z", base_asset="A", entry_price=100, initial_risk=10, side=1, net_return=-2.0),
        dict(trade_id="later", entry_time="2024-01-01T02:00Z", exit_time="2024-01-01T03:00Z", base_asset="B", entry_price=100, initial_risk=10, side=1, net_return=.20),
    ]
    result = _run(rows)
    ledger = result["ledger"].set_index("trade_id")
    assert result["summary"]["bankrupt"] and result["summary"]["final_balance"] == 0
    assert ledger.loc["wipeout", "raw_balance_after_exit"] == pytest.approx(-1000)
    assert ledger.loc["later", "rejection_reason"] == "entry_floor"


def test_same_timestamp_order_is_seeded_and_input_order_independent():
    rows = [
        dict(trade_id=name, entry_time="2024-01-01T00:00Z", exit_time="2024-01-01T01:00Z", base_asset=name, entry_price=100, initial_risk=5, side=1, net_return=0)
        for name in ("a", "b", "c", "d")
    ]
    first = simulate_shared_account(_trades(rows), sizing="fixed", risk_fraction=.05, seed=17)["ledger"]
    second = simulate_shared_account(_trades(list(reversed(rows))), sizing="fixed", risk_fraction=.05, seed=17)["ledger"]
    selected_first = set(first.loc[first.selected, "trade_id"])
    selected_second = set(second.loc[second.selected, "trade_id"])
    assert len(selected_first) == 2  # 5% + 5% reaches the 10% portfolio-risk cap.
    assert selected_first == selected_second
    assert first.set_index("trade_id").selection_hash.to_dict() == second.set_index("trade_id").selection_hash.to_dict()


def test_censored_trade_is_a_zero_pnl_boundary_mark_not_a_realized_exit():
    rows = [
        dict(trade_id="boundary", entry_time="2024-01-01T00:00Z", exit_time="2024-01-01T01:00Z", base_asset="A", entry_price=100, initial_risk=10, side=1, net_return=.90, censored=True),
    ]
    result = _run(rows)
    row = result["ledger"].iloc[0]
    assert row.selected and row.boundary_mark
    assert row.realized_pnl == 0
    assert result["summary"]["censored_boundary"] == 1
    assert result["summary"]["final_balance"] == 1000


def test_censored_column_is_optional_as_documented():
    rows = [
        dict(trade_id="closed", entry_time="2024-01-01T00:00Z", exit_time="2024-01-01T01:00Z", base_asset="A", entry_price=100, initial_risk=10, side=1, net_return=.10),
    ]
    result = _run(rows)
    assert result["summary"]["closed"] == 1
    assert result["summary"]["censored_boundary"] == 0
    assert result["summary"]["final_balance"] == pytest.approx(1100)


def test_random_small_ledger_is_invariant_to_input_order():
    """A compact reference invariant for the former timestamp-scan event loop."""
    start = pd.Timestamp("2024-01-01T00:00Z")
    rows = []
    for number in range(36):
        entry_offset = (number * 7) % 13  # Deliberate collisions exercise same-time hashes.
        rows.append(dict(
            trade_id=f"random-{number}", entry_time=start + pd.Timedelta(hours=entry_offset),
            exit_time=start + pd.Timedelta(hours=entry_offset + 1 + number % 4),
            base_asset=f"asset-{number % 7}", entry_price=100 + number % 3,
            initial_risk=(2, 5, 10)[number % 3], side=1 if number % 2 else -1,
            net_return=(-.18, -.04, 0, .07, .22)[number % 5], censored=number % 11 == 0,
        ))
    frame = _trades(rows)
    baseline = simulate_shared_account(frame, sizing="compound", risk_fraction=.03, seed=91)
    shuffled = simulate_shared_account(frame.sample(frac=1, random_state=43), sizing="compound", risk_fraction=.03, seed=91)
    assert baseline["summary"] == shuffled["summary"]
    pd.testing.assert_frame_equal(baseline["ledger"], shuffled["ledger"])
    pd.testing.assert_frame_equal(baseline["equity_curve"], shuffled["equity_curve"])
    pd.testing.assert_frame_equal(baseline["daily_realized_pnl"], shuffled["daily_realized_pnl"])
