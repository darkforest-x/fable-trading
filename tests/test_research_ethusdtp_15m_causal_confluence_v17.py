"""Contracts for the causal ETHUSDT.P 15m confluence experiments."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.research_ethusdtp_15m_causal_confluence_v17 import (
    CONFIG_PATH as V17_CONFIG_PATH,
)
from scripts.research_ethusdtp_15m_causal_confluence_v17 import (
    _aggregate_complete,
    _merge_htf,
)
from scripts.research_ethusdtp_15m_expansion_confluence_v18 import (
    CONFIG_PATH as V18_CONFIG_PATH,
)
from scripts.research_ethusdtp_15m_expansion_confluence_v18 import _add_expansion_scores


def test_higher_timeframe_feature_appears_only_at_bucket_close() -> None:
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=8, freq="15min", tz="UTC"),
            "open": range(8),
            "high": range(1, 9),
            "low": range(8),
            "close": range(1, 9),
            "volume": [1.0] * 8,
            "segment_id": [1] * 8,
        }
    )
    aggregate = _aggregate_complete(
        frame,
        rule="1h",
        expected_bars=4,
        ema_length=1,
        sma_length=1,
        slope_bars=1,
        return_bars=1,
    )

    assert aggregate["available_time"].tolist() == [
        pd.Timestamp("2024-01-01 01:00:00+00:00"),
        pd.Timestamp("2024-01-01 02:00:00+00:00"),
    ]

    decisions = pd.DataFrame(
        {
            "decision_time": pd.to_datetime(
                ["2024-01-01 00:59:00Z", "2024-01-01 01:00:00Z"]
            )
        }
    )
    merged = _merge_htf(
        decisions,
        aggregate,
        prefix="eth_1h",
        tolerance=pd.Timedelta("4h"),
    )
    assert pd.isna(merged.loc[0, "eth_1h_fast"])
    assert merged.loc[1, "eth_1h_fast"] == pytest.approx(2.0)


def test_v18_expansion_score_is_the_minimum_not_a_vote() -> None:
    events = pd.DataFrame(
        {
            "eth_atr_ratio96": [1.20, 0.70],
            "eth_bb_width_ratio96": [0.80, 1.30],
        }
    )
    scored = _add_expansion_scores(events)

    assert scored["expansion_floor"].tolist() == pytest.approx([0.80, 0.70])
    assert scored["expansion_geometric"].tolist() == pytest.approx(
        [(1.20 * 0.80) ** 0.5, (0.70 * 1.30) ** 0.5]
    )


def test_experiments_freeze_execution_and_exclude_holdout() -> None:
    v17 = json.loads(V17_CONFIG_PATH.read_text(encoding="utf-8"))
    v18 = json.loads(V18_CONFIG_PATH.read_text(encoding="utf-8"))

    assert v17["only_selected_variable"] == "confluence_gate_id"
    assert v17["frozen_system"]["changed_by_this_experiment"] is False
    assert v18["only_selected_variable"] == "expansion_floor_threshold"
    assert v18["fixed_candidate"]["threshold"] == pytest.approx(0.85)
    assert v18["frozen_system"]["changed_by_this_experiment"] is False
    assert v18["splits"]["audit_end_exclusive"] < v18["safety"]["holdout_start"]
    assert v18["safety"]["repository_holdout_rows_allowed"] == 0
    assert v18["safety"]["tradingview_change"] is False
