"""Contracts for the bounded OKX swap/spot 15m feed-sensitivity audit."""
import json

import pandas as pd

from scripts.analyze_pine_eth_15m_feed_sensitivity import CSV_OUTPUT, JSON_OUTPUT


def test_feed_audit_is_bounded_and_does_not_claim_parity() -> None:
    payload = json.loads(JSON_OUTPUT.read_text(encoding="utf-8"))
    assert payload["common_bars"] == 23_328
    assert payload["holdout_rows_read"] == 0
    assert payload["swap_quality"]["non_15m_gaps"] == 0
    assert payload["spot_quality"]["non_15m_gaps"] == 0
    assert payload["tradingview_parity_passed"] is False
    assert payload["spot_is_perpetual_substitute"] is False


def test_price_only_v9_is_more_feed_stable_than_volume_gated_v10() -> None:
    payload = json.loads(JSON_OUTPUT.read_text(encoding="utf-8"))
    entries = payload["executed_entry_comparisons"]
    assert entries["V9"]["jaccard"] > 0.95
    assert entries["V11"]["jaccard"] > 0.95
    assert entries["V10"]["jaccard"] < entries["V9"]["jaccard"]
    assert entries["V10"]["mean_absolute_net_return_delta_bp"] > entries["V9"][
        "mean_absolute_net_return_delta_bp"
    ]

    table = pd.read_csv(CSV_OUTPUT)
    assert set(table["variant"]) == {"V9", "V10", "V11"}
    assert set(table["feed"]) == {"swap", "spot"}
    assert len(table) == 6
