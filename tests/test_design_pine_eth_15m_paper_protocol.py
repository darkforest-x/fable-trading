"""The paper protocol must remain blocked, immutable, and non-executing."""
import json
from pathlib import Path

from scripts.design_pine_eth_15m_paper_protocol import OUTPUT, PROJECT


def test_paper_protocol_starts_nothing_and_fails_closed() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["holdout_rows_read"] == 0
    assert payload["formal_collection_started"] is False
    assert payload["forward_log_written"] is False
    assert payload["live_or_paper_order_sent"] is False
    assert payload["blocked"] is True
    assert payload["official_pine_compiler_run"] is True
    assert "trade export" in payload["blocking_gate"]
    assert payload["tradingview_parity_passed"] is False
    assert payload["forward_eligible"] is False
    assert payload["combined_v10_v11_arm_allowed"] is False


def test_paper_arms_are_hashed_and_require_slow_formal_read() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    arms = payload["arms"]
    assert set(arms) == {"V9", "V10", "V11"}
    for arm in arms.values():
        assert Path(PROJECT / arm["source"]).is_file()
        assert len(arm["sha256"]) == 64
        assert arm["minimum_fresh_trades_for_formal_read"] == 100
        assert arm["planning_months_to_100_fresh_trades"] > 12
        assert arm["historical_rate_is_forecast"] is False
    assert payload["formal_evaluation"]["familywise_alpha"] == 0.01
    assert payload["formal_evaluation"]["multiple_arm_adjustment"].startswith("Holm")
