"""Statistical contracts for the ETH 15m development-only robustness audit."""
from __future__ import annotations

import pandas as pd

from scripts.analyze_pine_eth_15m_robustness import (
    exact_block_signflip,
    prequential_feature_replay,
    selection_adjusted_max_signflip,
)


def _search_frame() -> pd.DataFrame:
    periods = ("2023H1", "2023H2", "2024H1", "2024H2")
    rows = []
    for feature, values, trades in (
        ("none", (10.0, 20.0, 30.0, 40.0), (20, 20, 20, 20)),
        ("stable", (15.0, 26.0, 37.0, 48.0), (15, 15, 15, 15)),
        ("unstable", (12.0, -30.0, 80.0, -20.0), (10, 10, 10, 10)),
    ):
        for period, value, count in zip(periods, values, trades):
            rows.append(
                {
                    "feature_filter": feature,
                    "period": period,
                    "project_net_bp_per_trade": value,
                    "trades": count,
                    "return_percent": value / 10.0,
                    "max_drawdown_15m_percent": 10.0,
                }
            )
    return pd.DataFrame(rows)


def test_exact_four_block_all_positive_signflip_has_floor_one_sixteenth() -> None:
    result = exact_block_signflip(pd.Series([1.0, 2.0, 3.0, 4.0]).to_numpy())
    assert result["p_value"] == 0.0625
    assert result["minimum_attainable_one_sided_p"] == 0.0625


def test_prequential_replay_never_selects_on_test_block() -> None:
    replay, summary = prequential_feature_replay(_search_frame())
    assert replay["selected_on_periods"].tolist() == [
        "2023H1",
        "2023H1,2023H2",
        "2023H1,2023H2,2024H1",
    ]
    assert replay["test_period"].tolist() == ["2023H2", "2024H1", "2024H2"]
    assert replay["selected_feature"].tolist() == ["stable", "stable", "stable"]
    assert summary["positive_increment_blocks"] == 3
    assert summary["increment_exact_signflip"]["p_value"] == 0.125


def test_max_stat_selection_adjustment_never_beats_unadjusted_p() -> None:
    search = _search_frame()
    result = selection_adjusted_max_signflip(search)
    assert result["selected_feature"] == "stable"
    assert result["candidate_gate_count_including_none"] == 3
    assert result["selection_adjusted_p_value"] >= result["selected_unadjusted"]["p_value"]
