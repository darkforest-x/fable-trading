"""Tests for the blocked V9/V12F prospective shadow protocol."""
from __future__ import annotations

from scripts.design_pine_eth_15m_paper_protocol_v2 import build_protocol


def test_v2_keeps_only_frozen_v9_and_v12f_and_starts_nothing() -> None:
    payload = build_protocol()
    assert payload["schema_version"] == "pine-eth-15m-paper-forward-v2"
    assert set(payload["arms"]) == {"V9", "V12F"}
    assert payload["comparison_scope"]["arms"] == ["V9", "V12F"]
    assert set(payload["comparison_scope"]["forbidden"]) == {
        "V10",
        "V11",
        "V12E",
        "V12T",
        "L2_model",
    }
    assert payload["combined_arm_allowed"] is False
    assert payload["blocked"] is True
    assert payload["formal_collection_started"] is False
    assert payload["activation_time"] is None
    assert payload["backfill_before_activation_allowed"] is False
    assert payload["forward_log_written"] is False
    assert payload["scanner_started"] is False
    assert payload["paper_or_live_order_sent"] is False
    assert payload["market_bar_rows_read"] == 0
    assert payload["compact_exposed_final_ledger_rows_read"] == 207
    assert payload["holdout_rows_read"] == 0
    assert payload["owner_approval"]["reference"] is None


def test_v2_binds_both_canonical_ledgers_and_exact_sources() -> None:
    payload = build_protocol()
    assert payload["arms"]["V9"]["canonical_preholdout_trades"] == 110
    assert payload["arms"]["V12F"]["canonical_preholdout_trades"] == 97
    for arm in payload["arms"].values():
        assert len(arm["source_sha256"]) == 64
        assert arm["minimum_fresh_trades_for_formal_read"] == 100
        assert arm["planning_months_to_100_fresh_trades"] > 12
        assert arm["historical_rate_is_forecast"] is False


def test_v2_requires_each_external_gate_and_forbids_interim_selection() -> None:
    payload = build_protocol()
    blockers = " | ".join(payload["blocking_gates"])
    assert "V9 exact 110-trade ledger parity" in blockers
    assert "V12F official compiler" in blockers
    assert "V12F exact 97-trade ledger parity" in blockers
    assert "owner explicitly approves" in blockers
    assert payload["arms"]["V9"]["compile_gate"]["parity_window_matches"] is False
    assert (
        payload["arms"]["V9"]["compile_gate"][
            "input_values_match_frozen_contract"
        ]
        is False
    )
    assert payload["parity_gate"]["exact_parity_passed"] is False
    assert payload["parity_gate"]["fee_semantics_verified_for_both"] is False
    assert (
        payload["shadow_accounting"][
            "project_20bp_cost_may_be_deducted_from_tradingview_net_again"
        ]
        is False
    )
    assert payload["formal_evaluation"]["familywise_alpha"] == 0.01
    assert payload["formal_evaluation"]["parameter_selection_from_interim_data_allowed"] is False
    assert payload["training_eligible"] is False
    assert payload["forward_eligible"] is False
    assert payload["production_eligible"] is False
