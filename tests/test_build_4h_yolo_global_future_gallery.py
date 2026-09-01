"""Tests for the delivery-only global-future 4h YOLO charts."""

from __future__ import annotations

import pandas as pd

from scripts.build_4h_yolo_global_future_gallery import (
    _gallery_document,
    bar_index,
    future_bar_count,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2026-08-01", periods=10, freq="4h", tz="UTC"),
        }
    )


def test_bar_index_uses_exact_utc_timestamp() -> None:
    assert bar_index(_frame(), "2026-08-01T20:00:00+00:00") == 5


def test_future_count_excludes_the_signal_bar() -> None:
    event = {"first_detection_bar_open_time": "2026-08-02T08:00:00+00:00"}
    assert future_bar_count(_frame(), event) == 1


def test_tip_signal_has_no_observed_future() -> None:
    event = {"first_detection_bar_open_time": "2026-08-02T12:00:00+00:00"}
    assert future_bar_count(_frame(), event) == 0


def test_gallery_labels_semantic_gate_survivors() -> None:
    document = _gallery_document(
        [
            {
                "event": {
                    "symbol": "TEST_USDT_SWAP",
                    "class_id": 0,
                    "is_current_latest_bar": False,
                    "confidence": 0.75,
                    "first_available_at": "2026-09-01T04:00:00+00:00",
                    "semantic_gate_pass": True,
                },
                "future_bars": 3,
                "chart": "charts_global_future/001_TEST_LONG.png",
            }
        ]
    )

    assert "4h YOLO + 因果语义门" in document
    assert "GATED" in document
    assert "只展示通过冻结因果语义门" in document
