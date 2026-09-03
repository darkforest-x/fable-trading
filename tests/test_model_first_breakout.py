"""Tests for the causal model-proposal-first breakout gate."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from yoyo.layers.l1_detection.data import ALL_MA_COLS
from yoyo.layers.l1_detection.model_first_breakout import (
    ModelFirstBreakoutError,
    decisions_equal,
    evaluate_model_first_breakout,
)


def frame(closes: list[float], edges: list[float]) -> pd.DataFrame:
    data: dict[str, list[float]] = {"close": closes}
    for offset, column in enumerate(ALL_MA_COLS):
        data[column] = [value - 0.05 + offset * 0.01 for value in edges]
    return pd.DataFrame(data)


def test_long_requires_first_close_above_entire_bundle() -> None:
    data = frame([9.9, 10.2], [10.0, 10.0])
    result = evaluate_model_first_breakout(data, proposal_end_i=1, direction="LONG")
    assert result.passed
    assert result.current_beyond_bundle
    assert result.previous_not_beyond_bundle


def test_already_above_is_rejected_as_late() -> None:
    data = frame([10.2, 10.3], [10.0, 10.0])
    result = evaluate_model_first_breakout(data, proposal_end_i=1, direction="LONG")
    assert not result.passed
    assert result.current_beyond_bundle
    assert not result.previous_not_beyond_bundle


def test_short_is_exact_directional_mirror() -> None:
    data = frame([10.1, 9.7], [10.0, 10.0])
    result = evaluate_model_first_breakout(data, proposal_end_i=1, direction="SHORT")
    assert result.passed


def test_future_rows_do_not_change_a_past_decision() -> None:
    original = frame([9.9, 10.2, 10.4], [10.0, 10.0, 10.1])
    mutated = original.copy()
    mutated.loc[2, :] = 1_000_000.0
    left = evaluate_model_first_breakout(original, proposal_end_i=1, direction="LONG")
    right = evaluate_model_first_breakout(mutated, proposal_end_i=1, direction="LONG")
    assert decisions_equal(left, right)


def test_invalid_endpoint_and_missing_mas_fail_closed() -> None:
    data = frame([9.9, 10.2], [10.0, 10.0])
    with pytest.raises(ModelFirstBreakoutError, match="prior row"):
        evaluate_model_first_breakout(data, proposal_end_i=0, direction="LONG")
    with pytest.raises(ModelFirstBreakoutError, match="missing columns"):
        evaluate_model_first_breakout(
            data.drop(columns=[ALL_MA_COLS[-1]]), proposal_end_i=1, direction="LONG"
        )


def test_official_diagnostic_freezes_model_first_pipeline_without_mutation() -> None:
    root = Path(__file__).resolve().parents[1]
    path = (
        root
        / "experiments/active/exp-1h-filusdt-model-first-breakout-gate-20260904-v1/preregistration.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["breakout_gate"]["pipeline_order"] == (
        "model_proposal_then_deterministic_breakout_gate"
    )
    assert payload["owner_authorization"]["holdout_consumption_number_for_checkpoint"] == 18
    assert payload["source_experiment"]["model_windows"] == 240
    assert not any(payload["safety"].values())
