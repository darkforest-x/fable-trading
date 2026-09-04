from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from scripts.backtest_btcusdtp_1h_owner_causal_v2_preholdout import (
    atr_quintiles,
    choose_protection_trigger,
    fee_to_risk_ratio,
    k2_wick_only_pass,
    path_pass,
    resolve_exit,
)


def _config(horizon: int = 4) -> dict:
    return {
        "execution": {
            "target_r": 3.0,
            "horizon_bars": horizon,
            "round_trip_cost_fraction": 0.002,
        }
    }


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"]).assign(
        open_time=pd.date_range("2025-01-01", periods=len(rows), freq="h", tz="UTC")
    )


def _long_event() -> dict:
    return {
        "entry_i": 0,
        "direction": 1,
        "entry_price": 100.0,
        "risk_price": 10.0,
        "risk_fraction": 0.1,
        "stop_price": 90.0,
    }


def test_k2_wick_only_requires_entire_body_on_directional_side() -> None:
    frame = pd.DataFrame(
        {
            "open": [101.0, 99.0, 99.0, 101.0],
            "close": [102.0, 102.0, 98.0, 98.0],
            "sma40_hl2": [100.0, 100.0, 100.0, 100.0],
        }
    )
    assert k2_wick_only_pass(frame, 0, 1)
    assert not k2_wick_only_pass(frame, 1, 1)
    assert k2_wick_only_pass(frame, 2, -1)
    assert not k2_wick_only_pass(frame, 3, -1)


def test_path_gate_requires_both_close_side_and_ma_colour_continuity() -> None:
    assert path_pass(
        {"wrong_sma40_close_count": 0, "intermediate_ma_colour_share": 1.0}
    )
    assert not path_pass(
        {"wrong_sma40_close_count": 1, "intermediate_ma_colour_share": 1.0}
    )
    assert not path_pass(
        {"wrong_sma40_close_count": 0, "intermediate_ma_colour_share": 0.75}
    )


def test_fee_to_risk_is_round_trip_cost_divided_by_risk_fraction() -> None:
    assert math.isclose(fee_to_risk_ratio(0.002, 400.0, 100_000.0), 0.5)
    assert math.isinf(fee_to_risk_ratio(0.002, 0.0, 100_000.0))


def test_atr_buckets_handle_boundary_month_with_fewer_than_five_rows() -> None:
    featured = pd.DataFrame(
        {
            "open_time": pd.to_datetime(
                ["2025-01-31T22:00:00Z", "2025-01-31T23:00:00Z"]
            ),
            "atr": [1.0, 2.0],
        }
    )
    buckets = atr_quintiles(featured, pd.Series([True, True]))
    assert buckets.tolist() == [0, 1]


def test_profit_protection_arms_on_close_and_acts_next_bar() -> None:
    featured = _bars(
        [
            (100.0, 116.0, 99.0, 115.0),
            (115.0, 118.0, 100.0, 101.0),
            (101.0, 105.0, 98.0, 103.0),
            (103.0, 106.0, 99.0, 104.0),
        ]
    )
    result = resolve_exit(
        featured,
        _long_event(),
        _config(),
        protection_trigger_r=1.5,
    )
    assert result["outcome"] == "protected_stop"
    assert result["exit_i"] == 1
    assert math.isclose(result["exit_price"], 100.2)
    assert math.isclose(result["net_return"], 0.0, abs_tol=1e-12)


def test_trigger_bar_cannot_retroactively_use_protection() -> None:
    featured = _bars(
        [
            (100.0, 116.0, 89.0, 115.0),
            (115.0, 118.0, 110.0, 116.0),
            (116.0, 120.0, 112.0, 118.0),
            (118.0, 121.0, 115.0, 119.0),
        ]
    )
    result = resolve_exit(
        featured,
        _long_event(),
        _config(),
        protection_trigger_r=1.5,
    )
    assert result["outcome"] == "sl"
    assert result["exit_i"] == 0
    assert result["exit_price"] == 90.0


def test_protection_selection_disables_when_improvement_gate_fails() -> None:
    trace = pd.DataFrame(
        [
            {
                "protection_trigger_r": float("nan"),
                "eligible": True,
                "robust_score_bp": 5.0,
                "worst_fold_net_bp": -2.0,
            },
            {
                "protection_trigger_r": 1.5,
                "eligible": True,
                "robust_score_bp": 5.5,
                "worst_fold_net_bp": -1.0,
            },
        ]
    )
    assert choose_protection_trigger(trace) == (
        None,
        "disabled_no_preregistered_improvement",
    )


def test_delivered_pine_keeps_source_style_and_frozen_causal_gates() -> None:
    project = Path(__file__).resolve().parents[1]
    pine = (
        project
        / "experiments/active/exp-btcusdtp-1h-owner-causal-v2-preholdout-20260904-v1"
        / "pine/fable_k1_k2_owner_causal_v2.pine"
    ).read_text(encoding="utf-8")
    for contract in (
        "k1BodyRatio >= 0.65",
        "k1MaAligned",
        "wrongSmaCloses == 0 and alignedMaBars == gap - 1",
        "k2TouchDepth >= 0.00",
        "k2BodyTrendSide",
        "longFeeToRisk <= feeToRiskMax",
        "shortFeeToRisk <= feeToRiskMax",
        'wickcolor = maShiftColor',
        'bordercolor = maShiftColor',
        '"MA Shift 40 · main"',
    ):
        assert contract in pine
    assert 'text = "TP"' not in pine
    assert 'text = "SL"' not in pine
