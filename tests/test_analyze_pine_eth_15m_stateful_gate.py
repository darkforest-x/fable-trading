"""A Pine judgment gate must be evaluated inside dynamic replay."""
import json

import pandas as pd

from scripts.analyze_pine_eth_15m_stateful_gate import OUTPUT_CSV, OUTPUT_JSON


def test_stateful_gate_audit_trains_nothing_and_stays_bounded() -> None:
    payload = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    assert payload["holdout_rows_read"] == 0
    assert payload["training_or_scoring_performed"] is False
    assert payload["barrier_parameters_changed"] is False
    assert payload["final_preholdout_already_consumed"] is True
    assert payload["static_top_decile_filtering_valid_for_l2"] is False


def test_static_filter_and_dynamic_replay_have_different_ledgers() -> None:
    payload = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    final = payload["final_summary"]
    assert final["vol_ratio_mean8_ge1"]["entry_jaccard"] < 0.90
    assert final["long_only"]["entry_jaccard"] < 1.0
    assert final["vol_ratio_mean8_ge1"]["static_minus_dynamic_net_bp"] > 0.0
    assert final["long_only"]["static_minus_dynamic_net_bp"] > 0.0

    table = pd.read_csv(OUTPUT_CSV)
    assert len(table) == 6
    assert set(table["gate"]) == {"vol_ratio_mean8_ge1", "long_only"}
    assert set(table["split"]) == {
        "discovery_2023",
        "confirmation_2024",
        "final_preholdout_2025_202602",
    }
