"""Unit tests for the frozen BTCUSDT.P 1h exit-extension replay."""
from __future__ import annotations

import pandas as pd
import pytest

from scripts.analyze_btcusdtp_1h_exit_extension import resolve_fixed, resolve_split


def _frame(*bars: tuple[float, float, float]) -> pd.DataFrame:
    return pd.DataFrame(bars, columns=["high", "low", "close"])


def test_fixed_target_hits_exact_barrier() -> None:
    result = resolve_fixed(
        _frame((135.0, 99.0, 132.0), (140.0, 120.0, 130.0)),
        entry_i=0,
        direction=1,
        entry_price=100.0,
        risk_price=10.0,
        target_r=3.0,
        horizon=2,
        cost=0.002,
    )
    assert result.outcome == "target"
    assert result.hold_bars == 1
    assert result.return_r == pytest.approx(3.0)
    assert result.net_return == pytest.approx(0.298)


def test_fixed_collision_is_conservative_stop() -> None:
    result = resolve_fixed(
        _frame((135.0, 89.0, 120.0)),
        entry_i=0,
        direction=1,
        entry_price=100.0,
        risk_price=10.0,
        target_r=3.0,
        horizon=1,
        cost=0.002,
    )
    assert result.outcome == "stop_collision"
    assert result.return_r == pytest.approx(-1.0)


def test_fixed_timeout_uses_last_close() -> None:
    result = resolve_fixed(
        _frame((110.0, 95.0, 105.0), (115.0, 96.0, 108.0)),
        entry_i=0,
        direction=1,
        entry_price=100.0,
        risk_price=10.0,
        target_r=3.0,
        horizon=2,
        cost=0.002,
    )
    assert result.outcome == "timeout"
    assert result.return_r == pytest.approx(0.8)


def test_split_3r_then_original_stop_realizes_one_r() -> None:
    result = resolve_split(
        _frame((131.0, 99.0, 125.0), (120.0, 89.0, 95.0)),
        entry_i=0,
        direction=1,
        entry_price=100.0,
        risk_price=10.0,
        second_target_r=6.0,
        horizon=2,
        cost=0.002,
    )
    assert result.outcome == "scale_then_stop"
    assert result.scaled_at_3r
    assert result.return_r == pytest.approx(1.0)
    assert result.net_return == pytest.approx(0.098)


def test_split_hits_both_targets_and_charges_cost_once() -> None:
    result = resolve_split(
        _frame((161.0, 99.0, 150.0)),
        entry_i=0,
        direction=1,
        entry_price=100.0,
        risk_price=10.0,
        second_target_r=6.0,
        horizon=1,
        cost=0.002,
    )
    assert result.outcome == "full_target"
    assert result.return_r == pytest.approx(4.5)
    assert result.gross_return == pytest.approx(0.45)
    assert result.net_return == pytest.approx(0.448)


def test_split_first_bar_collision_is_full_stop() -> None:
    result = resolve_split(
        _frame((131.0, 89.0, 120.0)),
        entry_i=0,
        direction=1,
        entry_price=100.0,
        risk_price=10.0,
        second_target_r=6.0,
        horizon=1,
        cost=0.002,
    )
    assert result.outcome == "stop_before_scale_collision"
    assert not result.scaled_at_3r
    assert result.return_r == pytest.approx(-1.0)


def test_runner_marks_half_at_3r_and_times_out_the_remainder() -> None:
    result = resolve_split(
        _frame((131.0, 99.0, 125.0), (125.0, 95.0, 120.0)),
        entry_i=0,
        direction=1,
        entry_price=100.0,
        risk_price=10.0,
        second_target_r=None,
        horizon=2,
        cost=0.002,
    )
    assert result.outcome == "scale_then_timeout"
    assert result.return_r == pytest.approx(2.5)
    assert result.net_return_r == pytest.approx(2.48)


def test_short_fixed_target_is_symmetric() -> None:
    result = resolve_fixed(
        _frame((101.0, 69.0, 75.0)),
        entry_i=0,
        direction=-1,
        entry_price=100.0,
        risk_price=10.0,
        target_r=3.0,
        horizon=1,
        cost=0.002,
    )
    assert result.outcome == "target"
    assert result.return_r == pytest.approx(3.0)
