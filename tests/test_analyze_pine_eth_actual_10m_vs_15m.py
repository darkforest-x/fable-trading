"""Contracts for the real 10m versus 15m common-window audit."""
import json

import pandas as pd

from scripts.analyze_pine_eth_actual_10m_vs_15m import (
    CONTROLS_CSV,
    OUTPUT_JSON,
    SUMMARY_CSV,
)


def test_actual_10m_is_exactly_built_and_bounded() -> None:
    payload = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    quality = payload["ten_minute_quality"]
    assert quality["source_5m_gaps"] == 0
    assert quality["parents_not_exactly_two_5m_bars"] == 0
    assert quality["aggregated_10m_gaps"] == 0
    assert quality["aggregated_10m_rows"] * 2 == quality["source_5m_rows"]
    assert payload["holdout_rows_read"] == 0
    assert payload["post_safe_rows_read"] == 0
    assert payload["tradingview_parity_passed"] is False
    assert payload["selection_or_promotion_allowed"] is False


def test_common_short_window_keeps_negative_timeframe_result_visible() -> None:
    payload = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    variants = payload["variants"]
    assert set(variants) == {"V8_10m", "V8_15m", "V9_10m", "V9_15m"}
    assert variants["V8_10m"]["summary"]["project_net_bp_per_trade"] > 0
    assert variants["V8_15m"]["summary"]["project_net_bp_per_trade"] < 0
    assert variants["V9_10m"]["summary"]["project_net_bp_per_trade"] < 0
    assert variants["V9_15m"]["summary"]["project_net_bp_per_trade"] < 0
    unavailable = set(payload["matched_control_unavailable_variants"])
    assert unavailable
    assert all(
        (not row["matched_control"]["available"] and row["matched_control"]["failure_reason"])
        or (
            row["matched_control"]["controls_per_trade_min"] == 3
            and row["matched_control"]["duplicate_control_starts"] == 0
            and row["week_signflip"]["p_value"] >= 0.01
        )
        for row in variants.values()
    )
    assert payload["isolated_deltas_bp_per_trade"]["V8_15m_minus_V8_10m"] < 0

    summary = pd.read_csv(SUMMARY_CSV)
    controls = pd.read_csv(CONTROLS_CSV)
    assert len(summary) == 4
    expected_rows = {
        label: int(trades * 3)
        for label, trades in summary.set_index("label")["trades"].items()
        if label not in unavailable
    }
    assert controls.groupby("variant_label").size().to_dict() == expected_rows
