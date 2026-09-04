"""Contracts for the positive super-trend harvest constraint."""

from __future__ import annotations

import json

import pytest

from scripts.research_ethusdtp_15m_positive_strength_harvest_v13 import CONFIG_PATH


def test_supertrend_release_budget_is_positive_but_capped_at_ten_percent() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    fractions = config["adaptive_harvest"]["effective_strong_stage_fractions"]

    assert fractions == pytest.approx([0.05, 0.025, 0.0125, 0.0125])
    assert sum(fractions) == pytest.approx(0.1)
    assert config["adaptive_harvest"]["strong_trend_release_multiplier"] == 0.25
    assert config["adaptive_harvest"]["stop_change_after_bank"] == "none"
