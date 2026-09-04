"""Static contracts for the V3 small-bank ladder."""

from __future__ import annotations

import json

from scripts.research_ethusdtp_15m_profit_seed_ladder_v3 import CONFIG_PATH


def test_seed_ladder_aligns_first_bank_with_runner_arm() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    assert config["progressive_scaleout"]["levels_atr"] == [2.0, 4.0, 6.0, 8.0]
    assert (
        config["progressive_scaleout"]["step_atr"]
        == config["frozen_execution"]["runner_arm_on_completed_close_atr"]
    )
    assert config["selection"]["factor"] == "bank_total_fraction"
    assert max(config["selection"]["bank_total_fraction_candidates"]) < 1.0
