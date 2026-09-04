from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.research_btcusdtp_k1k2_15m_dynamic_stop import (
    paired_familywise_signflip,
    resolve_stop_policy,
)


def config() -> dict:
    return {
        "execution_frozen": {
            "horizon_bars": 4,
            "target_r": 3.0,
            "round_trip_cost_fraction": 0.002,
            "baseline_profit_protection_trigger_close_r": 1.5,
        },
        "factor": {"baseline_label": "baseline"},
        "multiple_comparison": {"resamples": 1000, "seed": 7},
    }


def event(direction: int = 1) -> dict:
    return {
        "setup_id": "x",
        "entry_i": 0,
        "direction": direction,
        "entry_price": 100.0,
        "stop_price": 99.0 if direction > 0 else 101.0,
        "risk_price": 1.0,
        "risk_fraction": 0.01,
        "stop_distance_atr": 1.0,
    }


def frame(rows: list[tuple[float, float, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=len(rows), freq="15min", tz="UTC"),
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
            "sma40_hl2": [row[4] for row in rows],
            "atr": [row[5] for row in rows],
        }
    )


BASELINE = {
    "label": "baseline",
    "kind": "baseline",
}
WICK_LADDER = {
    "label": "wick",
    "kind": "dynamic_r_ladder",
    "trigger_source": "running favourable intrabar high or low",
    "levels": [
        {"trigger_r": 1.5, "locked_r": "fee_cover"},
        {"trigger_r": 2.0, "locked_r": 0.5},
        {"trigger_r": 2.5, "locked_r": 1.0},
    ],
}
CLOSE_LADDER = {
    "label": "close",
    "kind": "dynamic_r_ladder",
    "trigger_source": "best completed close in R",
    "levels": WICK_LADDER["levels"],
}
SOFT = {
    "label": "soft",
    "kind": "conditional_soft_structure_stop",
    "catastrophe_buffer_atr": 0.25,
}


def test_baseline_fee_cover_is_next_bar_and_economically_flat() -> None:
    prices = frame(
        [
            (100.0, 101.7, 99.4, 101.5, 100.0, 1.0),
            (101.5, 101.8, 100.1, 100.4, 100.0, 1.0),
            (100.4, 101.0, 100.0, 100.5, 100.0, 1.0),
            (100.5, 101.0, 100.0, 100.5, 100.0, 1.0),
        ]
    )
    result = resolve_stop_policy(prices, event(), config(), BASELINE)
    assert result["outcome"] == "protected_stop"
    assert result["hold_bars"] == 2
    assert result["gross_return"] == pytest.approx(0.002)
    assert result["net_return"] == pytest.approx(0.0)


def test_wick_ladder_update_does_not_apply_inside_trigger_bar() -> None:
    prices = frame(
        [
            (100.0, 101.7, 99.4, 100.5, 100.0, 1.0),
            (100.5, 101.0, 100.1, 100.4, 100.0, 1.0),
            (100.4, 101.0, 100.0, 100.5, 100.0, 1.0),
            (100.5, 101.0, 100.0, 100.5, 100.0, 1.0),
        ]
    )
    result = resolve_stop_policy(prices, event(), config(), WICK_LADDER)
    assert result["outcome"] == "dynamic_stop"
    assert result["hold_bars"] == 2
    assert result["net_return"] == pytest.approx(0.0)


def test_close_ladder_does_not_treat_a_wick_as_a_close_trigger() -> None:
    prices = frame(
        [
            (100.0, 101.7, 99.4, 100.5, 100.0, 1.0),
            (100.5, 100.8, 98.8, 99.0, 100.0, 1.0),
            (99.0, 100.0, 98.8, 99.2, 100.0, 1.0),
            (99.2, 100.0, 98.8, 99.2, 100.0, 1.0),
        ]
    )
    result = resolve_stop_policy(prices, event(), config(), CLOSE_LADDER)
    assert result["outcome"] == "sl"
    assert result["gross_return"] == pytest.approx(-0.01)


def test_soft_structure_stop_allows_only_valid_close_reclaim() -> None:
    prices = frame(
        [
            (100.0, 100.8, 98.9, 99.5, 99.2, 1.0),
            (99.5, 103.2, 99.4, 102.0, 100.0, 1.0),
            (102.0, 102.5, 101.0, 102.0, 100.0, 1.0),
            (102.0, 102.5, 101.0, 102.0, 100.0, 1.0),
        ]
    )
    result = resolve_stop_policy(prices, event(), config(), SOFT)
    assert result["outcome"] == "tp"
    assert result["structural_touches"] == 1
    assert result["valid_structure_reclaims"] == 1


def test_soft_structure_invalid_close_exits_at_next_open() -> None:
    prices = frame(
        [
            (100.0, 100.4, 98.9, 98.9, 99.2, 1.0),
            (99.4, 100.0, 99.0, 99.5, 99.2, 1.0),
            (99.5, 100.0, 99.0, 99.5, 99.2, 1.0),
            (99.5, 100.0, 99.0, 99.5, 99.2, 1.0),
        ]
    )
    result = resolve_stop_policy(prices, event(), config(), SOFT)
    assert result["outcome"] == "structure_invalid_next_open"
    assert result["exit_price"] == pytest.approx(99.4)
    assert result["hold_bars"] == 2


def test_short_soft_reclaim_is_mirrored() -> None:
    prices = frame(
        [
            (100.0, 101.1, 99.4, 100.5, 100.8, 1.0),
            (100.5, 100.6, 96.8, 98.0, 100.0, 1.0),
            (98.0, 99.0, 97.5, 98.0, 100.0, 1.0),
            (98.0, 99.0, 97.5, 98.0, 100.0, 1.0),
        ]
    )
    result = resolve_stop_policy(prices, event(-1), config(), SOFT)
    assert result["outcome"] == "tp"
    assert result["valid_structure_reclaims"] == 1


def test_familywise_test_keeps_identical_entry_keys() -> None:
    base = pd.DataFrame({"setup_id": ["a", "b", "c", "d"], "net_return": [-0.01, -0.01, 0.01, 0.01]})
    better = base.assign(net_return=base["net_return"] + 0.002)
    worse = base.assign(net_return=base["net_return"] - 0.001)
    cfg = config()
    cfg["factor"]["arms"] = [
        {"label": "baseline"},
        {"label": "better"},
        {"label": "worse"},
    ]
    result = paired_familywise_signflip(
        {"baseline": base, "better": better, "worse": worse}, cfg
    ).set_index("stop_policy")
    assert result.loc["better", "paired_mean_improvement_bp"] == pytest.approx(20.0)
    assert result.loc["worse", "paired_mean_improvement_bp"] == pytest.approx(-10.0)
    assert np.isfinite(result["familywise_signflip_p_one_sided"]).all()
