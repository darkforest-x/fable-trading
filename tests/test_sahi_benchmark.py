from __future__ import annotations

from pathlib import Path

import pytest

from src.detection.sahi_benchmark import (
    BenchmarkRecord,
    normalize_xyxy,
    select_image_paths,
    summarize_records,
)


def test_select_image_paths_is_sorted_for_full_and_deterministic_for_sample() -> None:
    paths = [Path("c.png"), Path("a.png"), Path("b.png")]

    assert select_image_paths(paths, limit=0, seed=7) == [Path("a.png"), Path("b.png"), Path("c.png")]
    assert select_image_paths(paths, limit=2, seed=7) == select_image_paths(paths, limit=2, seed=7)


def test_normalize_xyxy_preserves_box_geometry() -> None:
    assert normalize_xyxy((10.0, 20.0, 30.0, 60.0), width=100, height=100) == pytest.approx(
        (0.2, 0.4, 0.2, 0.4)
    )


def test_summarize_records_reports_custom_iou50_rates_and_latency() -> None:
    result = summarize_records(
        [
            BenchmarkRecord("direct", "a.png", 2, 3, 1, 0.4),
            BenchmarkRecord("direct", "b.png", 1, 1, 1, 0.6),
        ]
    )

    assert result["n_images"] == 2
    assert result["recall_like_iou50"] == pytest.approx(2 / 3)
    assert result["precision_like_iou50"] == pytest.approx(1 / 2)
    assert result["pred_per_gt"] == pytest.approx(4 / 3)
    assert result["seconds_per_image"] == pytest.approx(0.5)
