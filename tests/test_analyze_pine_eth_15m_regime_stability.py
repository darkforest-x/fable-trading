"""Contracts for frozen V9 chronological regime-stability evidence."""
import json

import pandas as pd

from scripts.analyze_pine_eth_15m_regime_stability import (
    CONTROLS_CSV,
    OUTPUT_JSON,
    SUMMARY_CSV,
    exhaustive_signflip,
)


def test_exact_signflip_enumerates_all_assignments() -> None:
    result = exhaustive_signflip([1.0, 2.0, -0.5])
    assert result["blocks"] == 3
    assert result["exact_assignments"] == 8
    assert 0.0 <= result["one_sided_p_value"] <= 1.0


def test_regime_stability_keeps_recent_failures_and_p_gate_visible() -> None:
    payload = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    assert payload["blocks"] == 9
    assert payload["holdout_rows_read"] == 0
    assert payload["barrier_parameters_changed"] is False
    assert payload["parameter_search_performed"] is False
    assert payload["selection_or_promotion_allowed"] is False
    assert payload["matched_controls_exact"] is True
    assert payload["absolute_net_equal_block_test"]["positive_blocks"] == 7
    assert payload["absolute_net_equal_block_test"]["one_sided_p_value"] >= 0.01
    assert payload["matched_excess_equal_block_test"]["one_sided_p_value"] >= 0.01
    assert {row["period"] for row in payload["recent_failures"]} == {
        "2025H1",
        "2026M1M2",
    }

    table = pd.read_csv(SUMMARY_CSV)
    controls = pd.read_csv(CONTROLS_CSV)
    assert len(table) == 9
    assert table["minimum_controls_per_trade"].eq(3).all()
    assert table["duplicate_control_starts"].eq(0).all()
    expected = table.set_index("period")["trades"].mul(3).astype(int).to_dict()
    assert controls.groupby("regime_period").size().to_dict() == expected
