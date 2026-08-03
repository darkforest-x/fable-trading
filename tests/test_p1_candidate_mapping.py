from __future__ import annotations

import pytest

from src.detection.render import ChartTransform
from src.judgment.yolo_candidates import WINDOW, map_box_to_signal


@pytest.fixture
def transform() -> ChartTransform:
    return ChartTransform(
        n_bars=WINDOW,
        width=1280,
        height=742,
        left=12,
        top=12,
        plot_w=1256,
        plot_h=718,
        price_min=90.0,
        price_max=110.0,
        candle_half_w=2,
    )


def _box_for_right_edge(tf: ChartTransform, bar_in_window: int) -> tuple[float, float]:
    width = 0.01
    right_norm = tf.x_at(bar_in_window) / tf.width
    return right_norm - width / 2.0, width


@pytest.mark.parametrize(
    ("window_end", "bar_in_window", "expected_signal", "expected_age"),
    [
        (500, 199, 500, 0),
        (500, 198, 499, 1),
        (498, 199, 498, 2),
    ],
)
def test_shared_mapper_accepts_global_tip_tip1_tip2(
    transform: ChartTransform,
    window_end: int,
    bar_in_window: int,
    expected_signal: int,
    expected_age: int,
):
    cx, width = _box_for_right_edge(transform, bar_in_window)
    mapped = map_box_to_signal(
        cx=cx,
        w=width,
        tf=transform,
        window_start_i=window_end - WINDOW + 1,
        n_bars=WINDOW,
        frame_length=501,
        latest_closed_i=500,
        tip_edge_bars=2,
        apply_tip_edge=True,
        max_global_tip_age_bars=2,
    )
    assert mapped.accepted is True
    assert mapped.mapped_signal_i == expected_signal
    assert mapped.global_tip_age_bars == expected_age


def test_shared_mapper_rejects_tip3_globally(transform: ChartTransform):
    cx, width = _box_for_right_edge(transform, 199)
    mapped = map_box_to_signal(
        cx=cx,
        w=width,
        tf=transform,
        window_start_i=497 - WINDOW + 1,
        n_bars=WINDOW,
        frame_length=501,
        latest_closed_i=500,
        max_global_tip_age_bars=2,
    )
    assert mapped.accepted is False
    assert mapped.rejection_reason == "global_tip_age"
    assert mapped.global_tip_age_bars == 3


def test_shared_mapper_keeps_local_tip_edge_gate(transform: ChartTransform):
    cx, width = _box_for_right_edge(transform, 197)
    mapped = map_box_to_signal(
        cx=cx,
        w=width,
        tf=transform,
        window_start_i=301,
        n_bars=WINDOW,
        frame_length=501,
        latest_closed_i=500,
        tip_edge_bars=2,
        apply_tip_edge=True,
        max_global_tip_age_bars=2,
    )
    assert mapped.accepted is False
    assert mapped.rejection_reason == "local_tip_edge"
