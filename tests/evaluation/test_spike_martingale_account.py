"""Synthetic-only causal accounting checks for the SPIKE martingale kernel."""

from __future__ import annotations

import copy

import pytest

from yoyo.evaluation.spike_martingale_account import simulate


def _trade(name: str, minute: int, net_return: float, risk: float = 1.0,
           reason: str = "initial_stop") -> dict[str, object]:
    return {
        "trade_id": name,
        "entry_time": f"2026-01-01T00:{minute:02d}:00+00:00",
        "exit_time": f"2026-01-01T00:{minute + 1:02d}:00+00:00",
        "net_return": net_return,
        "initial_risk_frac": risk,
        "exit_reason": reason,
    }


def test_cap_loss_resets_level_without_erasing_the_realized_loss() -> None:
    out = simulate(
        [_trade("a", 0, -1), _trade("b", 2, -1), _trade("c", 4, -1)],
        base_risk_usdt=10, multiplier=2, max_level=2,
    )
    assert [row["planned_risk"] for row in out["ledger"]] == [10, 20, 40]
    assert out["summary"]["final_balance"] == 930
    assert out["summary"]["capped_cycle_resets"] == 1
    assert out["summary"]["ending_level"] == 0
    assert out["ledger"][-1]["capped_cycle_reset"] is True
    assert out["ledger"][-1]["cycle_pnl_before_reset"] == -70


def test_fixed_cash_risk_changes_notional_with_stop_distance() -> None:
    out = simulate([_trade("near", 0, 0, .1), _trade("far", 2, 0, .2)])
    assert [row["planned_risk"] for row in out["ledger"]] == [10, 10]
    assert [row["notional"] for row in out["ledger"]] == [100, 50]


def test_future_outcomes_and_current_outcome_do_not_change_entry_sizing() -> None:
    prefix = [_trade("a", 0, -1), _trade("b", 2, 0.1), _trade("c", 4, -1)]
    changed_future = copy.deepcopy(prefix)
    changed_future[-1]["net_return"] = 99
    changed_current = copy.deepcopy(prefix)
    changed_current[1]["net_return"] = -0.9
    original = simulate(prefix)
    future = simulate(changed_future)
    current = simulate(changed_current)
    assert [row["notional"] for row in original["ledger"][:2]] == [10, 20]
    assert [row["notional"] for row in future["ledger"][:2]] == [10, 20]
    assert original["ledger"][1]["notional"] == current["ledger"][1]["notional"] == 20


def test_net_return_is_not_charged_a_second_fee() -> None:
    out = simulate([_trade("a", 0, .1, .1)])
    assert out["ledger"][0]["notional"] == 100
    assert out["ledger"][0]["pnl"] == 10
    assert out["summary"]["final_balance"] == 1010


def test_insufficient_balance_keeps_level_and_does_not_disclose_result() -> None:
    out = simulate(
        [_trade("reject", 0, -1, .1), _trade("later", 2, -1)],
        initial_balance=10.1, base_risk_usdt=10,
    )
    first, second = out["ledger"]
    assert first["accepted"] is False and first["reason"] == "insufficient_balance"
    assert first["pnl"] is None and "exit_time" not in first
    assert second["level"] == 0
    assert out["summary"]["n_accepted"] == 1


def test_win_resets_level_and_recovery_waits_for_real_cycle_recovery() -> None:
    win = simulate([_trade("loss", 0, -1), _trade("win", 2, .1)])
    assert [row["level"] for row in win["ledger"]] == [0, 1]
    assert win["summary"]["ending_level"] == 0

    recovery = simulate(
        [_trade("loss", 0, -.5), _trade("small-win", 2, .1), _trade("back", 4, .2)],
        reset_mode="recovery",
    )
    assert [row["level"] for row in recovery["ledger"]] == [0, 1, 1]
    assert recovery["summary"]["ending_level"] == 0


def test_only_a_negative_stop_exit_advances_the_level() -> None:
    out = simulate([
        _trade("non-stop-loss", 0, -.5, reason="time_limit"),
        _trade("trailing-stop-gap", 2, -.5, reason="trailing_stop_gap"),
    ])
    assert [row["level"] for row in out["ledger"]] == [0, 0]
    assert out["summary"]["ending_level"] == 1


def test_ruin_is_absorbing_and_invalid_and_overlapping_streams_fail_closed() -> None:
    ruined = simulate([_trade("zero", 0, -100), _trade("later", 2, 1)])
    assert ruined["summary"]["ruined"] is True
    assert ruined["ledger"][1]["reason"] == "ruined"
    assert ruined["summary"]["n_accepted"] == 1

    overlap = [_trade("a", 0, 0), _trade("b", 0, 0)]
    with pytest.raises(ValueError, match="overlap"):
        simulate(overlap)
    with pytest.raises(ValueError, match="initial_risk_frac"):
        simulate([_trade("bad", 0, 0, 0)])
    with pytest.raises(ValueError, match="initial_balance"):
        simulate([], initial_balance=-1)


def test_same_direction_needs_no_extra_admission_rule_and_fixed_risk_baseline() -> None:
    # Side is intentionally absent: the frozen stream has already imposed its
    # non-overlap contract, and this cash kernel adds no directional filter.
    out = simulate(
        [_trade("a", 0, -.5), _trade("b", 2, -.5)],
        multiplier=1, max_level=0,
    )
    assert [row["level"] for row in out["ledger"]] == [0, 0]
    assert [row["planned_risk"] for row in out["ledger"]] == [10, 10]
