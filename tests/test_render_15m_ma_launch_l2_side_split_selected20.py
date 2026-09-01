"""Tests for the frozen side-split q90 decision gallery."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import scripts.render_15m_ma_launch_l2_side_split_selected20 as module


def sample_rows() -> pd.DataFrame:
    records = []
    for side, count in module.EXPECTED_COUNTS.items():
        for index in range(count):
            records.append(
                {
                    "episode_id": f"{side}-{index}",
                    "symbol": "BTC_USDT_SWAP",
                    "side": side,
                    "split": "final_validation",
                    "dependency_representative": True,
                    "l2_keep": True,
                    "side_percentile_score": 1.0 - index / 100,
                    "available_at": f"2026-04-01T{index:02d}:00:00+00:00",
                }
            )
    records.append(
        {
            "episode_id": "not-kept",
            "symbol": "BTC_USDT_SWAP",
            "side": "long",
            "split": "final_validation",
            "dependency_representative": True,
            "l2_keep": False,
            "side_percentile_score": 0.1,
            "available_at": "2026-04-01T23:00:00+00:00",
        }
    )
    return pd.DataFrame(records)


def test_selection_requires_keep_and_dependency_representative() -> None:
    selected = module.select_frozen_q90_events(sample_rows())
    assert len(selected) == 20
    assert selected["side"].value_counts().to_dict() == {"long": 13, "short": 7}
    assert selected["l2_keep"].all()
    assert selected["dependency_representative"].all()


def test_selection_rejects_side_count_drift() -> None:
    frame = sample_rows().query("episode_id != 'short-0'")
    with pytest.raises(module.Selected20RenderError, match="side counts drifted"):
        module.select_frozen_q90_events(frame)


def test_string_false_is_not_treated_as_true() -> None:
    frame = sample_rows()
    frame["l2_keep"] = frame["l2_keep"].map({True: "true", False: "false"})
    selected = module.select_frozen_q90_events(frame)
    assert "not-kept" not in set(selected["episode_id"])


def test_contact_sheet_preserves_declared_two_column_geometry() -> None:
    image = np.full((1250, 1920, 3), 255, dtype=np.uint8)
    sheet = module.contact_sheet([image] * 3, side="long")
    assert sheet.shape == (
        module.CONTACT_HEADER_HEIGHT + 2 * module.CONTACT_TILE_HEIGHT,
        2 * module.CONTACT_TILE_WIDTH,
        3,
    )
