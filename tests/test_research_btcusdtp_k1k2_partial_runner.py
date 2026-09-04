from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import resolve_exit
from scripts.research_btcusdtp_k1k2_partial_runner import (
    resolve_runner_exit,
    select_runner,
)


def config() -> dict:
    return {
        "factor": {"first_take_r": 3.0, "runner_target_r": 8.0},
        "timeframe_fixed": {"5m": {"horizon_bars": 4}},
        "execution_frozen": {
            "target_r": 3.0,
            "round_trip_cost_fraction": 0.002,
            "profit_protection_trigger_close_r": 1.5,
        },
    }


def frame(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    times = pd.date_range("2024-01-01", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "open_time": times,
            "high": [row[0] for row in rows],
            "low": [row[1] for row in rows],
            "close": [row[2] for row in rows],
        }
    )


def event() -> dict:
    return {
        "entry_i": 0,
        "direction": 1,
        "entry_price": 100.0,
        "risk_price": 1.0,
        "risk_fraction": 0.01,
        "stop_price": 99.0,
    }


def test_zero_runner_matches_standard_3r_endpoint() -> None:
    prices = frame([(101.0, 99.5, 100.8), (103.2, 100.5, 102.0), (104.0, 101.0, 103.0), (104.0, 102.0, 103.0)])
    expected = resolve_exit(prices, event(), config(), "5m")
    actual = resolve_runner_exit(prices, event(), config(), "5m", 0.0)
    assert actual["gross_return"] == pytest.approx(expected["gross_return"])
    assert actual["net_return"] == pytest.approx(expected["net_return"])
    assert actual["outcome"] == expected["outcome"]


def test_full_runner_matches_standard_8r_endpoint() -> None:
    prices = frame([(102.0, 99.5, 101.6), (104.0, 100.0, 103.0), (108.2, 102.0, 107.0), (109.0, 106.0, 108.0)])
    endpoint = config()
    endpoint["execution_frozen"]["target_r"] = 8.0
    expected = resolve_exit(prices, event(), endpoint, "5m")
    actual = resolve_runner_exit(prices, event(), config(), "5m", 1.0)
    assert actual["gross_return"] == pytest.approx(expected["gross_return"])
    assert actual["net_return"] == pytest.approx(expected["net_return"])
    assert actual["outcome"] == expected["outcome"]


def test_partial_runner_weights_barrier_fills_and_charges_cost_once() -> None:
    prices = frame([(103.2, 99.5, 102.0), (108.2, 102.0, 107.0), (109.0, 106.0, 108.0), (109.0, 107.0, 108.0)])
    actual = resolve_runner_exit(prices, event(), config(), "5m", 0.25)
    expected_gross = 0.75 * 0.03 + 0.25 * 0.08
    assert actual["gross_return"] == pytest.approx(expected_gross)
    assert actual["net_return"] == pytest.approx(expected_gross - 0.002)
    assert actual["runner_outcome"] == "runner_tp"


def test_stop_target_collision_is_stop_first() -> None:
    prices = frame([(108.2, 98.8, 104.0), (104.0, 100.0, 102.0), (103.0, 100.0, 102.0), (103.0, 100.0, 102.0)])
    actual = resolve_runner_exit(prices, event(), config(), "5m", 0.5)
    assert actual["outcome"] == "sl_ambiguous"
    assert actual["gross_return"] == pytest.approx(-0.01)
    assert "ambiguous_stop_first" in actual["runner_outcome"]


def test_full_runner_ignores_zero_size_first_take_for_ambiguity() -> None:
    prices = frame([(103.2, 98.8, 101.0), (102.0, 100.0, 101.0), (102.0, 100.0, 101.0), (102.0, 100.0, 101.0)])
    endpoint = config()
    endpoint["execution_frozen"]["target_r"] = 8.0
    expected = resolve_exit(prices, event(), endpoint, "5m")
    actual = resolve_runner_exit(prices, event(), config(), "5m", 1.0)
    assert expected["outcome"] == "sl"
    assert actual["outcome"] == expected["outcome"]
    assert actual["gross_return"] == pytest.approx(expected["gross_return"])


def selection_row(fraction: float, robust: float, worst: float) -> dict:
    return {
        "runner_fraction": fraction,
        "eligible": True,
        "robust_score_bp": robust,
        "worst_fold_net_bp": worst,
        "events": 300,
    }


def test_selector_requires_both_improvement_and_fold_guard() -> None:
    baseline = selection_row(0.0, -10.0, -12.0)
    selected, reason = select_runner(
        [baseline, selection_row(0.5, -7.0, -15.01)], baseline
    )
    assert selected["runner_fraction"] == pytest.approx(0.0)
    assert reason.startswith("retain")


def test_runner_return_is_affine_between_endpoints() -> None:
    prices = frame([(102.0, 99.5, 101.6), (104.0, 100.0, 103.0), (108.2, 102.0, 107.0), (109.0, 106.0, 108.0)])
    returns = np.array(
        [resolve_runner_exit(prices, event(), config(), "5m", f)["gross_return"] for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
    )
    assert np.diff(returns) == pytest.approx(np.repeat(np.diff(returns)[0], 4))
