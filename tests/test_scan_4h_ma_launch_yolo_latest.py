"""Regression tests for latest-endpoint and confirmed-bar time semantics."""

from __future__ import annotations

import pandas as pd

from scripts.scan_4h_ma_launch_yolo_latest import (
    BAR_DELTA,
    CANVAS_HEIGHT,
    INSET_HEIGHT,
    LazyTaskSequence,
    build_tasks,
    deduplicate,
    delivery_context_start,
)


def _candidate(*, endpoint: str, confidence: float) -> dict[str, object]:
    return {
        "symbol": "TEST_USDT_SWAP",
        "core_end_i": 100,
        "class_name": "dense_long",
        "class_id": 0,
        "confidence": confidence,
        "window_end_time": endpoint,
    }


def test_latest_signal_uses_latest_endpoint_not_event_peak() -> None:
    events = deduplicate(
        [
            _candidate(endpoint="2026-09-01T00:00:00+00:00", confidence=0.91),
            _candidate(endpoint="2026-09-01T04:00:00+00:00", confidence=0.62),
        ]
    )

    assert len(events) == 1
    event = events[0]
    assert event["confidence"] == 0.62
    assert event["event_peak_confidence"] == 0.91
    assert event["window_end_time"] == "2026-09-01T04:00:00+00:00"


def test_confirmed_signal_availability_is_bar_open_plus_four_hours() -> None:
    event = deduplicate(
        [_candidate(endpoint="2026-09-01T04:00:00+00:00", confidence=0.62)]
    )[0]

    bar_open = pd.Timestamp(event["last_detection_bar_open_time"])
    available = pd.Timestamp(event["last_available_at"])
    assert available - bar_open == BAR_DELTA
    assert available == pd.Timestamp("2026-09-01T08:00:00+00:00")


def test_chart_canvas_has_room_for_exact_input_inset() -> None:
    inset_y = 926 + 18
    assert inset_y + INSET_HEIGHT <= CANVAS_HEIGHT


def test_long_lookback_tasks_render_lazily(monkeypatch) -> None:
    import numpy as np

    import scripts.scan_4h_ma_launch_yolo_latest as scanner

    periods = 140
    close = np.linspace(10.0, 20.0, periods)
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-08-01", periods=periods, freq="4h", tz="UTC"),
            "open": close - 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.arange(periods, dtype=float) + 1.0,
        }
    )
    calls: list[int] = []

    def fake_render(window, *, out_path=None):
        calls.append(len(window))
        return np.zeros((8, 8, 3), dtype=np.uint8), object()

    monkeypatch.setattr(scanner, "render_chart", fake_render)
    _, tasks = build_tasks({"TEST_USDT_SWAP": frame}, lookback_endpoints=2)

    assert isinstance(tasks, LazyTaskSequence)
    assert len(tasks) == 4
    assert calls == []
    batch = tasks[:2]
    assert calls == [18, 19]
    assert [item[2]["window_len"] for item in batch] == [18, 19]


def test_delivery_context_keeps_old_scored_window_visible() -> None:
    assert delivery_context_start(frame_length=299, window_start_i=191) == 191
    assert delivery_context_start(frame_length=299, window_start_i=280) == 219
