"""Focused causal and geometry tests for MA-box Review50."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.datasets.ma_launch_ma_box_review import (
    BASELINE_IMGSZ,
    CORE_LENGTHS,
    DENSITY_BARS,
    SOURCE_TO_MODEL_SCALE,
    WINDOW_BARS,
    MABoxReviewError,
    ma_box_for_span,
    select_tightest_span,
    stable_crop_end_offset,
    validate_owner_review_payload,
    window_bounds,
)
from yoyo.layers.l1_detection.render import make_chart_transform


MA_COLUMNS = ("sma20", "sma60", "sma120", "ema20", "ema60", "ema120")


def frame() -> pd.DataFrame:
    n = WINDOW_BARS
    close = np.linspace(100.0, 101.0, n)
    out = pd.DataFrame(
        {
            "open": close - 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
        }
    )
    for offset, column in enumerate(MA_COLUMNS):
        out[column] = 100.2 + np.linspace(0.0, 0.3, n) + offset * 0.01
    return out


def test_fixed_window_contains_complete_prior_density_span() -> None:
    for sample_id in ("a", "b", "c", "d", "e"):
        start, end, offset = window_bounds(100, sample_id)
        assert end - start + 1 == WINDOW_BARS
        assert offset in {0, 1, 2}
        assert start <= 100 - DENSITY_BARS
        assert end >= 100


def test_tight_span_is_invariant_to_t_and_future_mutation() -> None:
    original = frame()
    anchor_local = 17
    mutated = original.copy()
    mutated.loc[anchor_local:, [*MA_COLUMNS, "close", "high", "low", "open"]] *= 50.0
    for length in CORE_LENGTHS:
        left = select_tightest_span(original, anchor_local=anchor_local, core_len=length)
        right = select_tightest_span(mutated, anchor_local=anchor_local, core_len=length)
        assert left == right
        assert -DENSITY_BARS <= left.start_offset <= left.end_offset <= -1
        assert left.end_local - left.start_local + 1 == length


def test_tight_span_rejects_unwarmed_six_ma_rows_instead_of_imputing() -> None:
    data = frame()
    data.loc[:, "sma120"] = np.nan
    with pytest.raises(MABoxReviewError, match="no finite MA span candidate"):
        select_tightest_span(data, anchor_local=17, core_len=5)


def test_ma_box_contains_all_ma_points_and_never_uses_candle_extremes() -> None:
    data = frame()
    anchor_local = 17
    span = select_tightest_span(data, anchor_local=anchor_local, core_len=5)
    transform = make_chart_transform(data)
    first = ma_box_for_span(transform, data, span, min_model_height_px=24)
    changed = data.copy()
    changed.loc[span.start_local : span.end_local, "high"] += 1000.0
    changed.loc[span.start_local : span.end_local, "low"] -= 1000.0
    # Keep the identical transform to isolate label geometry inputs.
    second = ma_box_for_span(transform, changed, span, min_model_height_px=24)
    assert first == second
    assert first["contains_all_six_ma_points"] is True


def test_minimum_height_is_defined_at_model_input_scale() -> None:
    data = frame()
    anchor_local = 17
    span = select_tightest_span(data, anchor_local=anchor_local, core_len=5)
    box = ma_box_for_span(make_chart_transform(data), data, span, min_model_height_px=24)
    assert BASELINE_IMGSZ == 960
    assert SOURCE_TO_MODEL_SCALE == 0.75
    assert box["baseline_model_height_px"] >= 24.0 - 1e-9
    assert box["scale_aug_0_9_height_px"] >= 21.6 - 1e-9


def test_crop_position_hash_is_stable() -> None:
    assert stable_crop_end_offset("sample-1") == stable_crop_end_offset("sample-1")
    assert 0 <= stable_crop_end_offset("sample-2") <= 2


def review_contract() -> tuple[list[dict[str, str]], dict[str, object]]:
    rows = [
        {
            "sample_id": "p1",
            "symbol": "BTC-USDT-SWAP",
            "direction": "LONG",
            "anchor_time": "2026-01-01T00:00:00+00:00",
            "image_sha256": "a" * 64,
        },
        {
            "sample_id": "p2",
            "symbol": "ETH-USDT-SWAP",
            "direction": "SHORT",
            "anchor_time": "2026-01-02T00:00:00+00:00",
            "image_sha256": "b" * 64,
        },
    ]
    answers = [
        {
            **row,
            "decision": "ACCEPT",
            "preferred_core_len": 5,
            "preferred_min_model_px": 24,
        }
        for row in rows
    ]
    payload: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": "exp-15m-ma-launch-ma-box-review50-v1",
        "prereg_sha256": "pre",
        "review_manifest_sha256": "manifest",
        "n_total": 2,
        "n_answered": 2,
        "complete": True,
        "answers": answers,
    }
    return rows, payload


def test_complete_owner_review_is_validated_but_does_not_unlock_training() -> None:
    rows, payload = review_contract()
    summary = validate_owner_review_payload(
        payload,
        rows,
        prereg_sha256="pre",
        review_manifest_sha256="manifest",
    )
    assert summary["n_reviewed"] == 2
    assert summary["preferred_core_length_counts"] == {"5": 2}
    assert summary["training_eligible"] is False
    assert summary["sample_owner_confirmed"] is False


def test_owner_review_rejects_render_identity_drift() -> None:
    rows, payload = review_contract()
    payload["answers"][0]["image_sha256"] = "c" * 64  # type: ignore[index]
    with pytest.raises(MABoxReviewError, match="identity drift"):
        validate_owner_review_payload(
            payload,
            rows,
            prereg_sha256="pre",
            review_manifest_sha256="manifest",
        )
