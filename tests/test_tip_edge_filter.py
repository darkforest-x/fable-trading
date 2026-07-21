"""A′ tip-edge gate: only last N bars of the scan window enter the ledger.

Source: analysis/p_box_to_bar_lag.md (KORU right_norm≈97.5% → bar offset 3).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from src.detection.render import ChartTransform
from src.judgment.yolo_candidates import (
    TIP_EDGE_BARS,
    WINDOW,
    right_edge_to_bar,
    scan_series_with_yolo,
)


def _tf(n_bars: int = WINDOW, width: int = 1280, height: int = 742) -> ChartTransform:
    left = top = 12
    return ChartTransform(
        n_bars=n_bars,
        width=width,
        height=height,
        left=left,
        top=top,
        plot_w=width - 2 * left,
        plot_h=height - 2 * top,
        price_min=100.0,
        price_max=110.0,
        candle_half_w=3,
    )


def _xywhn_for_bar(tf: ChartTransform, bar: int, w_norm: float = 0.02) -> list[float]:
    """Build xywhn whose right edge maps to `bar` via right_edge_to_bar."""
    right_px = float(tf.x_at(bar))
    right_norm = right_px / tf.width
    return [right_norm - w_norm / 2.0, 0.5, w_norm, 0.1]


def _frame(n: int = 500) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [1.0] * n,
            "high": [1.1] * n,
            "low": [0.9] * n,
            "close": [1.0] * n,
            "volume": [100.0] * n,
        }
    )


def _predict_with_bars(tf: ChartTransform, bars: list[int]) -> MagicMock:
    model = MagicMock()
    res = MagicMock()
    xywhn = np.array([_xywhn_for_bar(tf, b) for b in bars], dtype=np.float64)
    boxes = MagicMock()
    boxes.xywhn = MagicMock()
    boxes.xywhn.cpu.return_value = MagicMock()
    boxes.xywhn.cpu.return_value.numpy.return_value = xywhn
    res.boxes = boxes
    model.predict.return_value = [res]
    return model


def test_tip_edge_bars_default_is_two() -> None:
    assert TIP_EDGE_BARS == 2


def test_right_edge_to_bar_roundtrip() -> None:
    """Regression: x_at ↔ right_edge_to_bar is exact for all bars (incl. non-square)."""
    tf = _tf(n_bars=WINDOW)
    for bar in range(WINDOW):
        cx, _, w, _ = _xywhn_for_bar(tf, bar)
        assert right_edge_to_bar(cx, w, tf, n_bars=WINDOW) == bar


def test_tip_edge_accepts_tip_and_tip_minus_one_live() -> None:
    """bar_in_win 199 (tip) and 198 (tip-1) pass; N=2 (min_gap=1 so both survive)."""
    tf = _tf()
    frame = _frame()
    tip = len(frame) - 1
    model = _predict_with_bars(tf, [WINDOW - 1, WINDOW - 2])
    with patch("src.judgment.yolo_candidates.add_mas", side_effect=lambda df: df), patch(
        "src.judgment.yolo_candidates.render_chart", return_value=(None, tf)
    ):
        out = scan_series_with_yolo(
            frame, model=model, mode="live", window=WINDOW, tip_edge_bars=2, min_gap=1
        )
    assert tip in out
    assert tip - 1 in out


def test_tip_edge_rejects_koru_offset_three() -> None:
    """KORU-class: bar_in_win=196 (offset 3) rejected on tip and live tip-window."""
    tf = _tf()
    frame = _frame()
    koru_bar = WINDOW - 4  # 196
    cx, _, w, _ = _xywhn_for_bar(tf, koru_bar)
    assert right_edge_to_bar(cx, w, tf, n_bars=WINDOW) == koru_bar
    model = _predict_with_bars(tf, [koru_bar])
    with patch("src.judgment.yolo_candidates.add_mas", side_effect=lambda df: df), patch(
        "src.judgment.yolo_candidates.render_chart", return_value=(None, tf)
    ):
        tip_out = scan_series_with_yolo(
            frame, model=model, mode="tip", window=WINDOW, tip_edge_bars=2
        )
    assert tip_out == []
    # live: same xywhn on every window is an unrealistic mock; assert the gate
    # drops bar_in_win=196 on the tip window via tip_edge_rejected bump.
    from src.judgment.yolo_candidates import get_tip_edge_rejected, reset_tip_edge_rejected

    reset_tip_edge_rejected()
    with patch("src.judgment.yolo_candidates.add_mas", side_effect=lambda df: df), patch(
        "src.judgment.yolo_candidates.render_chart", return_value=(None, tf)
    ):
        scan_series_with_yolo(frame, model=model, mode="live", window=WINDOW, tip_edge_bars=2)
    assert get_tip_edge_rejected() >= 1


def test_tip_mode_accepts_tip_minus_one() -> None:
    """tip mode still needs entry bar, so tip-1 (198) is the accept case."""
    tf = _tf()
    frame = _frame()
    tip = len(frame) - 1
    model = _predict_with_bars(tf, [WINDOW - 2])
    with patch("src.judgment.yolo_candidates.add_mas", side_effect=lambda df: df), patch(
        "src.judgment.yolo_candidates.render_chart", return_value=(None, tf)
    ):
        out = scan_series_with_yolo(frame, model=model, mode="tip", window=WINDOW, tip_edge_bars=2)
    assert out == [tip - 1]


def test_full_mode_keeps_mid_window_boxes() -> None:
    """Offline full builds must not apply the A′ gate."""
    tf = _tf()
    frame = _frame(n=500)
    koru_bar = WINDOW - 4
    model = _predict_with_bars(tf, [koru_bar])
    with patch("src.judgment.yolo_candidates.add_mas", side_effect=lambda df: df), patch(
        "src.judgment.yolo_candidates.render_chart", return_value=(None, tf)
    ):
        out = scan_series_with_yolo(frame, model=model, mode="full", window=WINDOW, stride=WINDOW)
    # first full start is WARMUP; mid-window box must still enter offline ledger
    from src.judgment.candidates import WARMUP_BARS

    assert WARMUP_BARS + koru_bar in out
