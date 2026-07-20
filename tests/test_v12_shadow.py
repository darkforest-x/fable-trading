"""Safety rails and tip-mode unit checks for H-TIP v12 shadow."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.judgment.forward import (
    FORWARD_LOG_PATH,
    FORWARD_LOG_V12_SHADOW_PATH,
    run_forward_tracking_v12_shadow,
)
from src.judgment.yolo_candidates import scan_series_with_yolo


def test_v12_shadow_refuses_mainline_log_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mainline"):
        run_forward_tracking_v12_shadow(output_path=FORWARD_LOG_PATH)


def test_v12_shadow_default_path_is_not_mainline() -> None:
    assert FORWARD_LOG_V12_SHADOW_PATH.resolve() != FORWARD_LOG_PATH.resolve()
    assert FORWARD_LOG_V12_SHADOW_PATH.name == "forward_log_v12_shadow.csv"


def test_tip_mode_renders_single_window() -> None:
    """tip mode must request only the rightmost window (CPU budget)."""
    n = 500  # need >= WARMUP(288)+WINDOW(200)+2
    frame = pd.DataFrame(
        {
            "open": [1.0] * n,
            "high": [1.1] * n,
            "low": [0.9] * n,
            "close": [1.0] * n,
            "volume": [100.0] * n,
        }
    )
    model = MagicMock()
    # Empty predict result list matching batch size
    empty = MagicMock()
    empty.boxes = None
    model.predict.return_value = [empty]

    with patch("src.judgment.yolo_candidates.add_mas", side_effect=lambda df: df), patch(
        "src.judgment.yolo_candidates.render_chart"
    ) as render:
        tf = MagicMock()
        tf.width = 1000
        tf.left = 0
        tf.plot_w = 1000
        render.return_value = (None, tf)
        out = scan_series_with_yolo(frame, model=model, mode="tip", window=200)
        assert out == []
        assert render.call_count == 1
        assert model.predict.call_count == 1
