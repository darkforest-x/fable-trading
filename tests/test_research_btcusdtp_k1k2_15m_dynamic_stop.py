from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.research_btcusdtp_k1k2_15m_dynamic_stop import (
    paired_familywise_signflip,
    resolve_stop_policy,
)

RESULTS_DIR = Path(
    "experiments/active/"
    "exp-btcusdtp-k1k2-15m-dynamic-stop-preholdout-20260904-v1/results"
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


def test_generated_receipt_preserves_parity_and_keeps_closed_periods_unread() -> None:
    receipt = json.loads((RESULTS_DIR / "development_receipt.json").read_text())

    assert receipt["holdout_rows_read"] == 0
    assert receipt["audit_rows_read"] == 0
    assert receipt["audit_open_allowed"] is False
    assert receipt["development_gate_passed"] is False
    assert receipt["selected_policy"] == "baseline_close_fee_cover_1p5r"
    assert receipt["tradingview_replacement_allowed"] is False
    assert receipt["predecessor_baseline_parity"] == {
        "entries": 100,
        "exit_price_max_abs_error": pytest.approx(1.4551915228366852e-11),
        "net_return_max_abs_error": pytest.approx(9.93129189996722e-17),
        "outcome_mismatches": 0,
        "setup_keys_exact": True,
    }


def test_generated_policy_metrics_reconcile_and_every_fold_is_negative() -> None:
    metrics = pd.read_csv(RESULTS_DIR / "development_policy_metrics.csv")
    folds = pd.read_csv(RESULTS_DIR / "development_policy_folds.csv")

    assert len(metrics) == 6
    assert len(folds) == 24
    assert (folds["mean_net_bp"] < 0).all()
    exit_columns = [
        "exit_target",
        "exit_original_stop",
        "exit_fee_cover_stop",
        "exit_dynamic_profit_stop",
        "exit_catastrophe_stop",
        "exit_structure_next_open",
        "exit_sma40_next_open",
        "exit_timeout",
    ]
    assert metrics[exit_columns].sum(axis=1).tolist() == metrics["events"].tolist()
    assert not metrics["all_halfyears_gross_positive"].any()


def test_generated_failure_contributions_reconcile_paired_policy_deltas() -> None:
    contributions = pd.read_csv(RESULTS_DIR / "development_failure_contributions.csv")
    paired = pd.read_csv(RESULTS_DIR / "development_policy_familywise_tests.csv").set_index(
        "stop_policy"
    )

    observed = contributions.groupby("stop_policy")[
        "policy_improvement_contribution_to_all_mean_bp"
    ].sum()
    for policy, row in paired.iterrows():
        assert observed.loc[policy] == pytest.approx(row["paired_mean_improvement_bp"])


def test_generated_classifier_is_strictly_expanding_time_forward() -> None:
    predictions = pd.read_csv(
        RESULTS_DIR / "development_early_failure_predictions.csv.gz",
        parse_dates=["entry_time"],
    )

    expected_training_events = {"2023H2": 22, "2024H1": 43, "2024H2": 70}
    assert set(predictions["test_fold"]) == set(expected_training_events)
    for fold, training_events in expected_training_events.items():
        assert set(predictions.loc[predictions["test_fold"] == fold, "training_events"]) == {
            training_events
        }
    assert predictions.groupby(["model", "setup_id"]).size().max() == 1
