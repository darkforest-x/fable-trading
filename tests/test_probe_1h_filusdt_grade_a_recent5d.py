"""Pure contract tests for the FIL 1h five-day model probe."""
from __future__ import annotations

import pandas as pd

from scripts.probe_1h_filusdt_grade_a_recent5d import (
    collapse_episodes,
    episodes_overlap,
    latest_closed_open,
    normalize_closed_frame,
)


def test_latest_closed_open_excludes_forming_hour() -> None:
    assert latest_closed_open("2026-09-03T15:55:00Z") == pd.Timestamp(
        "2026-09-03T14:00:00Z"
    )


def test_normalize_closed_frame_drops_forming_row() -> None:
    times = pd.date_range("2026-08-27T00:00:00Z", periods=181, freq="1h")
    frame = pd.DataFrame(
        {
            "open_time": times,
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.0,
            "volume": 10.0,
        }
    )
    closed = normalize_closed_frame(frame, "2026-09-03T12:37:00Z")
    assert closed.iloc[-1]["open_time"] == pd.Timestamp("2026-09-03T11:00:00Z")


def test_episode_representative_is_first_available_not_later_peak() -> None:
    rows = [
        {
            "core_start_i": 100,
            "window_end_i": 106,
            "window_len": 19,
            "window_end_time": "2026-09-01T01:00:00Z",
            "confidence": 0.51,
            "class_name": "dense_long",
        },
        {
            "core_start_i": 102,
            "window_end_i": 108,
            "window_len": 18,
            "window_end_time": "2026-09-01T03:00:00Z",
            "confidence": 0.96,
            "class_name": "dense_long",
        },
    ]
    events = collapse_episodes(rows, "structural")
    assert len(events) == 1
    assert events[0]["window_end_i"] == 106
    assert events[0]["episode_max_confidence"] == 0.96
    assert events[0]["first_available_at"] == "2026-09-01T02:00:00+00:00"


def test_episode_overlap_allows_later_semantic_confirmation() -> None:
    structural = {"core_start_i": 100, "window_end_i": 106}
    pipeline = {"core_start_i": 102, "window_end_i": 109}
    separate = {"core_start_i": 110, "window_end_i": 116}
    assert episodes_overlap(structural, pipeline)
    assert not episodes_overlap(structural, separate)
