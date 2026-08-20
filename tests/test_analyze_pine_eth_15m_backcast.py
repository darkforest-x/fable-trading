"""Contracts for the 2022 reverse-time transport diagnostic."""
import json

import pandas as pd

from scripts.analyze_pine_eth_15m_backcast import (
    CONTROLS_CSV,
    OUTPUT_JSON,
    SUMMARY_CSV,
)


def test_backcast_reads_no_later_period_and_claims_no_oos() -> None:
    payload = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    assert payload["post_backcast_rows_read"] == 0
    assert payload["discovery_rows_read"] == 0
    assert payload["final_preholdout_rows_read"] == 0
    assert payload["holdout_rows_read"] == 0
    assert payload["selection_occurs_after_evaluation_period"] is True
    assert payload["is_out_of_sample"] is False
    assert payload["may_change_locked_candidate"] is False
    assert payload["barrier_parameters_changed"] is False


def test_backcast_has_exact_controls_and_keeps_significance_failure() -> None:
    payload = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    for row in payload["variants"].values():
        assert row["matched_control"]["controls_per_trade_min"] == 3
        assert row["matched_control"]["duplicate_control_starts"] == 0
        assert row["week_signflip"]["p_value"] >= 0.01

    v9 = payload["variants"]["V9"]
    assert v9["summary"]["project_net_bp_per_trade"] > 0
    assert v9["matched_control"]["candidate_minus_control_bp"] > 0
    assert v9["summary"]["project_net_bp_per_trade"] > payload["variants"]["V10"][
        "summary"
    ]["project_net_bp_per_trade"]

    summaries = pd.read_csv(SUMMARY_CSV)
    controls = pd.read_csv(CONTROLS_CSV)
    assert set(summaries["variant"]) == {"V9", "V10", "V11"}
    expected = {"V9": 80 * 3, "V10": 70 * 3, "V11": 42 * 3}
    assert controls.groupby("variant_label").size().to_dict() == expected
    assert not controls.groupby("variant_label")["control_signal_i"].apply(
        lambda values: values.duplicated().any()
    ).any()
