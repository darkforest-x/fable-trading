"""Contracts for the micro fixed-profit ladder."""

from __future__ import annotations

import json

from scripts.research_ethusdtp_15m_micro_profit_ladder_v16 import CONFIG_PATH


def test_micro_ladder_preserves_at_least_eighty_percent_runner() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    candidates = config["selection"]["bank_total_fraction_candidates"]

    assert candidates == [0.10, 0.15, 0.20]
    assert max(candidates) <= 0.20
    assert config["bank_only_scaleout"]["levels_atr"] == [2.0, 4.0, 8.0, 12.0]


def test_profit_ladder_never_changes_stop() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    assert config["bank_only_scaleout"]["stop_change_after_bank"] == "none"
    assert config["selection"]["success_gates"]["candidate_p95_net_retention_min"] == 0.95


def test_repository_holdout_is_physically_excluded() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    assert config["source_contract"]["safe_end_exclusive"] < config["source_contract"]["holdout_start"]
