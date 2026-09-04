"""Contracts for the micro fixed-profit ladder."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.research_ethusdtp_15m_micro_profit_ladder_v16 import CONFIG_PATH

PINE = (
    Path(__file__).resolve().parents[1]
    / "experiments/active/exp-ethusdtp-15m-micro-profit-ladder-preholdout-20260905-v16/pine/fable_eth_15m_trend_gradual_tp_v16.pine"
)


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


def test_pine_keeps_source_style_and_has_no_tp_sl_text() -> None:
    source = PINE.read_text(encoding="utf-8")

    assert source.startswith("//@version=6\n")
    assert "color MA_UP = #17A297" in source
    assert "color MA_DOWN = color.orange" in source
    assert "wickcolor = maColour" in source
    assert "bordercolor = maColour" in source
    assert '"TP"' not in source
    assert '"SL"' not in source
    assert source.count("plotcandle(") == 1


def test_pine_partial_block_never_writes_the_stop() -> None:
    source = PINE.read_text(encoding="utf-8")
    partial_block = source.split("if hitsNow > 0 and totalBankPct > 0.0", 1)[1].split(
        "float signedCloseProfitAtr", 1
    )[0]

    assert "remainingPct :=" in partial_block
    assert "activeStop :=" not in partial_block
    assert all(token in source for token in ("2.0", "4.0", "8.0", "12.0"))
