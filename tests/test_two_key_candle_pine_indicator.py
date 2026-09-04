from __future__ import annotations

from scripts.validate_two_key_candle_pine_indicator import validate


def test_pine_indicator_static_compile_and_owner_anchor_parity() -> None:
    payload = validate()

    assert payload["status"] == "pass"
    assert all(payload["static_checks"].values())
    assert all(payload["compile_checks"].values())
    assert all(row["passes"] for row in payload["anchor_parity"])
    assert payload["null_control"]["result"]["owner_anchor_matches"] == 2
    assert payload["null_control"]["result"]["reverse_direction_null_matches"] == 0
