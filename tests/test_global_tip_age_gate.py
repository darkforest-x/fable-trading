"""P0.7 acceptance G-01..G-04: local edge is not global freshness."""
from __future__ import annotations

import pandas as pd
import pytest

import src.judgment.forward_scan as fs
from src.judgment.yolo_candidates import (
    enforce_global_tip_age,
    get_global_tip_age_rejected,
    reset_global_tip_age_rejected,
)


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


def test_g02_global_tip_age_zero_one_two_are_accepted() -> None:
    reset_global_tip_age_rejected()
    assert enforce_global_tip_age([499, 498, 497], latest_closed_i=499, max_age_bars=2) == [
        497,
        498,
        499,
    ]
    assert get_global_tip_age_rejected() == 0


def test_g03_local_edge_candidate_at_global_tip_minus_three_is_rejected() -> None:
    reset_global_tip_age_rejected()
    assert enforce_global_tip_age([496], latest_closed_i=499, max_age_bars=2) == []
    assert get_global_tip_age_rejected() == 1


def test_g01_forward_final_gate_rejects_back2_local_bar198_mapping(monkeypatch) -> None:
    frame = _frame()
    # A window starting at global tip-window-start-2 plus local bar 198 maps
    # to global tip-3. It passes the local 2-bar edge but must fail globally.
    tip = len(frame) - 1
    global_tip_minus_three = tip - 3
    monkeypatch.setattr(
        fs, "scan_series_with_yolo", lambda *args, **kwargs: [global_tip_minus_three]
    )
    reset_global_tip_age_rejected()
    assert fs.forward_candidate_indices(
        frame, frame=frame, yolo_model=object(), yolo_mode="live", max_tip_age_bars=2
    ) == []
    assert get_global_tip_age_rejected() == 1


def test_g04_local_and_global_reject_counters_are_distinct() -> None:
    from src.judgment.yolo_candidates import get_tip_edge_rejected, reset_tip_edge_rejected

    reset_tip_edge_rejected()
    reset_global_tip_age_rejected()
    enforce_global_tip_age([10], latest_closed_i=20, max_age_bars=2)
    assert get_tip_edge_rejected() == 0
    assert get_global_tip_age_rejected() == 1


def test_negative_age_cap_is_rejected() -> None:
    with pytest.raises(ValueError):
        enforce_global_tip_age([1], latest_closed_i=2, max_age_bars=-1)

