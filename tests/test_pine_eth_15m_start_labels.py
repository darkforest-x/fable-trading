"""Focused path-ordering tests for automatic Pine V9 start labels."""
from __future__ import annotations

import pandas as pd
import pytest

from scripts.audit_pine_eth_15m_start_labels import (
    LABEL_TARGET_PERCENT,
    next_opposite_signal_indices,
    validate_fifteen_minute_cadence,
)
from yoyo.layers.l2_judgment.pine_start_labels import (
    label_pine_start_candidate,
    rounded_initial_stop_distance,
)


def _frame(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["open_time", "open", "high", "low", "close"],
    ).assign(
        open_time=lambda frame: pd.to_datetime(frame["open_time"], utc=True),
        atr=1.0,
    )


def _base_frame(*, entry_high: float = 101.0, entry_low: float = 99.0) -> pd.DataFrame:
    return _frame(
        [
            ("2024-01-01T00:00:00Z", 100.0, 100.5, 99.5, 100.0),
            ("2024-01-01T00:15:00Z", 100.0, entry_high, entry_low, 100.0),
            ("2024-01-01T00:30:00Z", 100.0, 101.0, 99.0, 100.0),
        ]
    )


def test_stop_distance_uses_signal_close_cap_and_tick_rounding() -> None:
    assert LABEL_TARGET_PERCENT == 1.5
    assert rounded_initial_stop_distance(signal_close=100.0, signal_atr=2.0) == 3.0
    assert rounded_initial_stop_distance(signal_close=100.0, signal_atr=0.333) == 1.33


def test_long_target_only_is_positive() -> None:
    outcome = label_pine_start_candidate(
        _base_frame(entry_high=101.6),
        signal_i=0,
        side="long",
        opposite_signal_i=None,
    )
    assert outcome.status == "resolved"
    assert outcome.label == 1
    assert outcome.outcome == "target"
    assert outcome.entry_time == pd.Timestamp("2024-01-01T00:15:00Z")
    assert outcome.target_price == pytest.approx(101.5)
    assert outcome.initial_stop_price == pytest.approx(97.0)


def test_short_target_only_is_positive() -> None:
    outcome = label_pine_start_candidate(
        _base_frame(entry_low=98.4),
        signal_i=0,
        side="short",
        opposite_signal_i=None,
    )
    assert outcome.label == 1
    assert outcome.target_price == pytest.approx(98.5)
    assert outcome.initial_stop_price == pytest.approx(103.0)


def test_ordered_three_minute_path_resolves_parent_target_before_stop() -> None:
    frame = _base_frame(entry_high=102.0, entry_low=95.0)
    parent_time = pd.Timestamp("2024-01-01T00:15:00Z")
    lower = _frame(
        [
            ("2024-01-01T00:15:00Z", 100.0, 101.0, 99.0, 100.5),
            ("2024-01-01T00:18:00Z", 100.5, 101.6, 100.0, 101.5),
            ("2024-01-01T00:21:00Z", 101.5, 102.0, 99.0, 100.0),
            ("2024-01-01T00:24:00Z", 100.0, 100.0, 95.0, 96.0),
            ("2024-01-01T00:27:00Z", 96.0, 97.0, 95.0, 96.0),
        ]
    )
    outcome = label_pine_start_candidate(
        frame,
        signal_i=0,
        side="long",
        opposite_signal_i=None,
        lower_by_parent={parent_time: lower},
    )
    assert outcome.status == "resolved"
    assert outcome.label == 1
    assert outcome.resolution == "ordered_lower_path"
    assert outcome.event_time == pd.Timestamp("2024-01-01T00:18:00Z")


def test_same_three_minute_bar_double_touch_stays_automatic_ambiguity() -> None:
    frame = _base_frame(entry_high=102.0, entry_low=95.0)
    parent_time = pd.Timestamp("2024-01-01T00:15:00Z")
    lower = _frame(
        [
            ("2024-01-01T00:15:00Z", 100.0, 102.0, 95.0, 100.0),
            ("2024-01-01T00:18:00Z", 100.0, 101.0, 99.0, 100.0),
            ("2024-01-01T00:21:00Z", 100.0, 101.0, 99.0, 100.0),
            ("2024-01-01T00:24:00Z", 100.0, 101.0, 99.0, 100.0),
            ("2024-01-01T00:27:00Z", 100.0, 101.0, 99.0, 100.0),
        ]
    )
    outcome = label_pine_start_candidate(
        frame,
        signal_i=0,
        side="long",
        opposite_signal_i=None,
        lower_by_parent={parent_time: lower},
    )
    assert outcome.status == "ambiguous"
    assert outcome.label is None
    assert outcome.resolution == "lower_bar_double_touch"


def test_opposite_signal_fills_after_its_signal_bar_path() -> None:
    no_target = label_pine_start_candidate(
        _base_frame(),
        signal_i=0,
        side="long",
        opposite_signal_i=1,
    )
    assert no_target.label == 0
    assert no_target.outcome == "opposite_signal_exit"
    assert no_target.event_time == pd.Timestamp("2024-01-01T00:30:00Z")

    target_on_opposite_signal_bar = label_pine_start_candidate(
        _base_frame(entry_high=101.6),
        signal_i=0,
        side="long",
        opposite_signal_i=1,
    )
    assert target_on_opposite_signal_bar.label == 1
    assert target_on_opposite_signal_bar.outcome == "target"


def test_no_event_before_development_end_is_censored_not_negative() -> None:
    outcome = label_pine_start_candidate(
        _base_frame(),
        signal_i=0,
        side="long",
        opposite_signal_i=None,
    )
    assert outcome.status == "censored"
    assert outcome.label is None
    assert outcome.outcome == "development_end_censored"


def test_next_opposite_signal_mapping_is_strictly_later() -> None:
    surface = pd.DataFrame(
        {
            "candidate_id": ["l10", "s10", "s20", "l30"],
            "side": ["long", "short", "short", "long"],
            "signal_i": [10, 10, 20, 30],
        }
    )
    mapping = next_opposite_signal_indices(surface)
    assert mapping["l10"] == 20
    assert mapping["s10"] == 30
    assert mapping["s20"] == 30
    assert mapping["l30"] is None


def test_parent_cadence_rejects_a_missing_fifteen_minute_bar() -> None:
    valid = _base_frame()
    assert validate_fifteen_minute_cadence(valid)["non_15m_gaps"] == 0
    broken = valid.drop(index=1).reset_index(drop=True)
    with pytest.raises(RuntimeError, match="15m cadence"):
        validate_fifteen_minute_cadence(broken)
