"""Endpoint and visibility-cutoff regression tests for the historical snapshot scan."""
import numpy as np
import pandas as pd
import pytest

from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
from yoyo.datasets.ma_launch_owner_perfect_filter import PerfectFilterError, extract_profile
from yoyo.datasets.ma_launch_snapshot_scan import closed_confirmation_indices, source_event_nms


@pytest.mark.parametrize("minutes", [60, 240])
def test_confirmation_interval_uses_closed_bar_time(minutes: int) -> None:
    times = pd.Series(pd.date_range("2026-08-19T00:00:00Z", periods=4, freq=f"{minutes}min"))
    target = times.iloc[1] + pd.Timedelta(minutes=minutes)
    assert closed_confirmation_indices(times, bar_minutes=minutes, start_utc=target, end_utc=target).tolist() == [1]


def test_extract_profile_accepts_explicit_visibility_cutoff_without_global_override() -> None:
    times = pd.date_range("2026-08-19T00:00:00Z", periods=200, freq="60min")
    base = np.linspace(100, 120, len(times))
    frame = add_candidate_features(pd.DataFrame({"open_time": times, "open": base, "high": base + 1, "low": base - 1, "close": base + .2, "volume": 1.0}))
    row = {"source_core_start_i": 150, "source_core_end_i": 153, "source_comparison_anchor_i": 155, "direction": "LONG", "box": {"h_norm": 0.0}}
    with pytest.raises(PerfectFilterError, match="visibility cutoff"):
        extract_profile(frame, row, bar_minutes=60)
    cutoff = frame["open_time"].iloc[158] + pd.Timedelta(hours=1, nanoseconds=1)
    assert extract_profile(frame, row, bar_minutes=60, visibility_end_exclusive=cutoff).core_end_i == 153


def test_source_event_nms_keeps_minimum_similarity_per_original_fixed_cluster() -> None:
    base = pd.Timestamp("2026-08-19T10:00:00Z")
    rows = [
        {"sample_id": "first", "source_path": "one.csv", "bar_minutes": 15, "direction": "LONG", "core_bars": 4, "similarity_distance": .4, "core_end_time": base.isoformat()},
        {"sample_id": "better", "source_path": "one.csv", "bar_minutes": 15, "direction": "LONG", "core_bars": 5, "similarity_distance": .2, "core_end_time": (base + pd.Timedelta(minutes=55)).isoformat()},
        {"sample_id": "next", "source_path": "one.csv", "bar_minutes": 15, "direction": "LONG", "core_bars": 4, "similarity_distance": .3, "core_end_time": (base + pd.Timedelta(minutes=60)).isoformat()},
    ]
    assert [row["sample_id"] for row in source_event_nms(rows)] == ["better", "next"]
