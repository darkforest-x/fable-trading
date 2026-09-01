from __future__ import annotations

import numpy as np

from scripts.research_15m_ma_launch_l2_feature_group_ablation import (
    FEATURE_ARMS,
    FEATURE_GROUPS,
    fractional_top_decile_metrics,
    load_preregistration,
    select_best_arm,
)
from yoyo.layers.l2_judgment.features import FEATURE_COLUMNS


def test_feature_groups_are_an_exact_partition_of_legacy_28() -> None:
    flattened = tuple(
        column
        for columns in FEATURE_GROUPS.values()
        for column in columns
    )
    assert flattened == tuple(FEATURE_COLUMNS)
    assert len(flattened) == len(set(flattened)) == 28


def test_preregistered_arms_match_code_and_expected_sizes() -> None:
    prereg = load_preregistration()
    assert prereg["selection"][
        "final_validation_used_for_selection"
    ] is False
    assert {
        name: len(columns) for name, columns in FEATURE_ARMS.items()
    } == {
        "ma_spread_only": 1,
        "ma_structure": 11,
        "context_only": 17,
        "ma_plus_trend": 16,
        "ma_plus_trend_volume": 19,
        "ma_plus_trend_volume_volatility": 24,
        "full_28": 28,
    }


def test_fractional_top_decile_is_invariant_inside_cutoff_tie() -> None:
    scores = np.array([9.0] * 4 + [1.0] * 16)
    returns_a = np.array([0.01, 0.03, -0.01, 0.05] + [0.0] * 16)
    returns_b = np.array([0.05, -0.01, 0.03, 0.01] + [0.0] * 16)
    labels = np.array([1, 1, 0, 1] + [0] * 16)
    first = fractional_top_decile_metrics(
        scores, returns_a, labels, 0.002
    )
    second = fractional_top_decile_metrics(
        scores, returns_b, labels, 0.002
    )
    assert first["effective_n"] == second["effective_n"] == 2.0
    assert first["boundary_fraction"] == second[
        "boundary_fraction"
    ] == 0.5
    assert first["net_mean"] == second["net_mean"]


def _record(
    arm: str,
    *,
    net: float,
    rho: float,
    count: int,
    healthy: bool = True,
) -> dict:
    return {
        "arm": arm,
        "feature_count": count,
        "health": {"passed": healthy},
        "diagnostics": {
            "fractional_top_decile": {"net_mean": net},
            "spearman_score_vs_return": rho,
        },
    }


def test_selection_uses_metric_then_preregistered_tie_breakers() -> None:
    records = [
        _record(
            "ma_structure", net=0.01, rho=0.2, count=11
        ),
        _record(
            "ma_plus_trend", net=0.01, rho=0.2, count=16
        ),
        _record(
            "full_28",
            net=0.50,
            rho=0.9,
            count=28,
            healthy=False,
        ),
    ]
    assert select_best_arm(records)["arm"] == "ma_structure"


def test_selection_falls_back_to_full28_if_all_arms_unhealthy() -> None:
    records = [
        _record(
            "ma_structure",
            net=0.02,
            rho=0.3,
            count=11,
            healthy=False,
        ),
        _record(
            "full_28",
            net=-0.02,
            rho=-0.1,
            count=28,
            healthy=False,
        ),
    ]
    assert select_best_arm(records)["arm"] == "full_28"


def test_round_trip_float_parser_preserves_score_plateau(tmp_path) -> None:
    import pandas as pd

    values = np.array(
        [
            0.00417856492785575,
            np.nextafter(0.00417856492785575, np.inf),
        ]
    )
    path = tmp_path / "scores.csv"
    pd.DataFrame({"score": values}).to_csv(path, index=False)
    loaded = pd.read_csv(
        path, float_precision="round_trip"
    )["score"].to_numpy(dtype=float)
    assert np.array_equal(values, loaded)
