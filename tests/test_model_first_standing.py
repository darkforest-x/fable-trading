"""Tests for the causal model-proposal-first MA-bundle position gate."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from yoyo.layers.l1_detection.data import ALL_MA_COLS
from yoyo.layers.l1_detection.model_first_standing import (
    ModelFirstStandingError,
    evaluate_model_first_standing,
    standing_decisions_equal,
)


def frame(closes: list[float], edges: list[float]) -> pd.DataFrame:
    data: dict[str, list[float]] = {"close": closes}
    for offset, column in enumerate(ALL_MA_COLS):
        data[column] = [value - 0.05 + offset * 0.01 for value in edges]
    return pd.DataFrame(data)


def test_long_passes_when_current_close_is_above_entire_bundle() -> None:
    data = frame([10.2, 10.3], [10.0, 10.0])
    result = evaluate_model_first_standing(data, proposal_end_i=1, direction="LONG")
    assert result.passed
    assert result.current_beyond_bundle


def test_previous_bar_is_deliberately_irrelevant() -> None:
    previous_below = frame([9.8, 10.3], [10.0, 10.0])
    previous_above = frame([100.0, 10.3], [10.0, 10.0])
    left = evaluate_model_first_standing(
        previous_below, proposal_end_i=1, direction="LONG"
    )
    right = evaluate_model_first_standing(
        previous_above, proposal_end_i=1, direction="LONG"
    )
    assert standing_decisions_equal(left, right)
    assert left.passed and right.passed


def test_short_is_exact_directional_mirror() -> None:
    data = frame([10.0, 9.7], [10.0, 10.0])
    result = evaluate_model_first_standing(data, proposal_end_i=1, direction="SHORT")
    assert result.passed


def test_equality_does_not_count_as_beyond_bundle() -> None:
    data = frame([10.0], [10.0])
    result = evaluate_model_first_standing(data, proposal_end_i=0, direction="LONG")
    assert not result.passed


def test_future_rows_do_not_change_a_past_decision() -> None:
    original = frame([9.9, 10.2, 10.4], [10.0, 10.0, 10.1])
    mutated = original.copy()
    mutated.loc[2, :] = 1_000_000.0
    left = evaluate_model_first_standing(original, proposal_end_i=1, direction="LONG")
    right = evaluate_model_first_standing(mutated, proposal_end_i=1, direction="LONG")
    assert standing_decisions_equal(left, right)


def test_invalid_endpoint_direction_and_missing_mas_fail_closed() -> None:
    data = frame([9.9, 10.2], [10.0, 10.0])
    with pytest.raises(ModelFirstStandingError, match="outside"):
        evaluate_model_first_standing(data, proposal_end_i=2, direction="LONG")
    with pytest.raises(ModelFirstStandingError, match="unsupported"):
        evaluate_model_first_standing(data, proposal_end_i=1, direction="FLAT")
    with pytest.raises(ModelFirstStandingError, match="missing columns"):
        evaluate_model_first_standing(
            data.drop(columns=[ALL_MA_COLS[-1]]),
            proposal_end_i=1,
            direction="LONG",
        )


def test_official_v2_diagnostic_removes_the_previous_bar_condition() -> None:
    root = Path(__file__).resolve().parents[1]
    path = (
        root
        / "experiments/active/exp-1h-filusdt-model-first-standing-gate-20260904-v2/preregistration.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["position_gate"]["pipeline_order"] == (
        "model_proposal_then_deterministic_bundle_position_gate"
    )
    assert payload["position_gate"]["prior_bar_condition"] is None
    assert payload["position_gate"]["lookback_rows"] == 1
    assert payload["owner_authorization"]["holdout_consumption_number_for_checkpoint"] == 19
    assert not any(payload["safety"].values())
