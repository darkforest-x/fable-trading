from __future__ import annotations

import pandas as pd
import pytest

from scripts.research_btcusdtp_k1k2_stop_buffer import (
    apply_execution_gates,
    select_buffer,
)


def config() -> dict:
    return {
        "execution_frozen": {
            "round_trip_cost_fraction": 0.002,
            "next_open_risk_atr_min": 0.15,
            "next_open_risk_atr_max": 2.50,
            "fee_to_risk_max": 10.0,
            "target_r": 3.0,
        },
        "timeframe_fixed": {"5m": {"cooldown_bars": 72}},
    }


def frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": pd.to_datetime(
                ["2024-01-01T00:00:00Z", "2024-01-01T00:05:00Z"]
            ),
            "open": [95.0, 100.0],
            "high": [110.0, 101.0],
            "low": [90.0, 99.0],
            "atr": [10.0, 10.0],
        }
    )


def candidate(direction: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "direction": direction,
                "k1_i": 0,
                "k2_i": 0,
                "gap_bars": 6,
                "secondary_score": 0.8,
            }
        ]
    )


def params() -> dict:
    return {
        "ma_period": 40,
        "gap_min_bars": 6,
        "gap_max_bars": 24,
        "score_floor": 0.4,
    }


@pytest.mark.parametrize(
    ("direction", "expected_stop", "expected_risk", "expected_target"),
    [(1, 88.0, 12.0, 136.0), (-1, 112.0, 12.0, 64.0)],
)
def test_buffer_is_outside_k2_extreme(
    direction: int,
    expected_stop: float,
    expected_risk: float,
    expected_target: float,
) -> None:
    accepted, decisions = apply_execution_gates(
        candidate(direction), frame(), config(), "5m", params(), 0.2
    )
    assert decisions.iloc[0]["decision"] == "accepted"
    assert accepted.iloc[0]["stop_price"] == pytest.approx(expected_stop)
    assert accepted.iloc[0]["risk_price"] == pytest.approx(expected_risk)
    assert accepted.iloc[0]["stop_distance_atr"] == pytest.approx(1.2)
    assert accepted.iloc[0]["target_price"] == pytest.approx(expected_target)


def test_zero_buffer_reproduces_exact_k2_extreme() -> None:
    long, _ = apply_execution_gates(
        candidate(1), frame(), config(), "5m", params(), 0.0
    )
    short, _ = apply_execution_gates(
        candidate(-1), frame(), config(), "5m", params(), 0.0
    )
    assert long.iloc[0]["stop_price"] == 90.0
    assert short.iloc[0]["stop_price"] == 110.0


def test_execution_gate_does_not_read_future_ohlc() -> None:
    original = frame()
    changed = frame()
    changed.loc[1, ["high", "low"]] = [10_000.0, 1.0]
    left, _ = apply_execution_gates(
        candidate(1), original, config(), "5m", params(), 0.3
    )
    right, _ = apply_execution_gates(
        candidate(1), changed, config(), "5m", params(), 0.3
    )
    columns = ["entry_price", "stop_price", "risk_price", "target_price"]
    assert left[columns].to_dict("records") == right[columns].to_dict("records")


def test_negative_buffer_is_refused() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        apply_execution_gates(candidate(1), frame(), config(), "5m", params(), -0.1)


def test_selection_requires_registered_improvement() -> None:
    baseline = {
        "k2_stop_buffer_atr": 0.0,
        "eligible": True,
        "robust_score_bp": -20.0,
        "worst_fold_net_bp": -22.0,
        "events": 300,
    }
    too_small = {
        "k2_stop_buffer_atr": 0.1,
        "eligible": True,
        "robust_score_bp": -18.5,
        "worst_fold_net_bp": -21.0,
        "events": 300,
    }
    selected, reason = select_buffer([baseline, too_small], baseline)
    assert selected is baseline
    assert reason.startswith("retain_zero")

    passing = {**too_small, "k2_stop_buffer_atr": 0.2, "robust_score_bp": -17.5}
    selected, reason = select_buffer([baseline, passing], baseline)
    assert selected is passing
    assert reason == "move_by_preregistered_rule"
