"""Tests for the blocked V9/V12F prospective shadow protocol."""
from __future__ import annotations

import json

import scripts.design_pine_eth_15m_paper_protocol_v2 as protocol_module
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
    assert payload["venue_owner_confirmed"] is True
    assert payload["venue_lock"]["owner_selected_symbol"] == "OKX:ETHUSDT.P"
    assert payload["venue_confirmation"]["paper_activation_approved"] is False


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
    assert "P0 semantic stability and P1 Gold Dataset" in blockers
    assert "owner confirms exact venue" not in blockers
    assert payload["project_stage_gate"]["paper_may_start_before_p0_p1_pass"] is False
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


def test_v2_venue_confirmation_is_narrow_and_fail_closed(
    tmp_path, monkeypatch
) -> None:
    invalid_confirmation = tmp_path / "owner_venue_confirmation.json"
    invalid_confirmation.write_text(
        json.dumps(
            {
                "schema_version": "pine-eth-15m-owner-venue-confirmation-v1",
                "confirmed": True,
                "confirmed_symbol": "BINANCE:ETHUSDT.P",
                "confirmed_timeframe": "15m",
                "compile_only": True,
                "paper_forward_activation_approved": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        protocol_module, "OWNER_VENUE_CONFIRMATION", invalid_confirmation
    )
    payload = protocol_module.build_protocol()
    assert payload["venue_owner_confirmed"] is False
    assert payload["venue_lock"]["owner_selected_symbol"] is None
    assert "owner confirms exact venue" in " | ".join(payload["blocking_gates"])
