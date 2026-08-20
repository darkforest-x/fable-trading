"""Canonical V9 Pine source must stay aligned with the frozen contract."""
import json

import pytest

from scripts.audit_pine_eth_15m_static_contract import OUTPUT, parse_constant


def test_parse_constant_requires_literal_definition() -> None:
    assert parse_constant("const float X = 1.5\n", "X") == 1.5
    with pytest.raises(ValueError, match="missing or non-literal"):
        parse_constant("float X = input.float(1.5)\n", "X")


def test_static_contract_passes_without_claiming_compiler_or_parity() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["status"] == "pass"
    assert payload["check_count"] == 25
    assert all(payload["checks"].values())
    assert payload["failed"] == []
    assert payload["official_pine_compiler_run"] is False
    assert payload["tradingview_parity_passed"] is False
    assert payload["production_eligible"] is False
    warning = payload["break_even_cost_warning"]
    assert warning["lock_bp"] == 10.0
    assert warning["frozen_round_trip_cost_bp"] == 20.0
    assert warning["project_net_if_filled_at_lock_bp"] == -10.0
    assert warning["is_true_project_net_break_even"] is False
