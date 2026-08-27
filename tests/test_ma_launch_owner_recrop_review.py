from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.datasets.ma_launch_owner_recrop_review import (
    EXPERIMENT_ID,
    OwnerRecropReviewError,
    core_box,
    resolve_decision,
    stable_context,
    validate_preregistration,
)
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS
from yoyo.layers.l1_detection.render import make_chart_transform


ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
SOURCE = ROOT / "experiments" / "active" / "exp-15m-ma-launch-ma-box-review50-v1" / "results" / "review_manifest.jsonl"


def frame() -> pd.DataFrame:
    close = np.linspace(100.0, 101.0, 18)
    out = pd.DataFrame(
        {
            "open": close - 0.08,
            "high": close + 0.25,
            "low": close - 0.28,
            "close": close,
            "volume": np.ones(18),
        }
    )
    for index, column in enumerate(SIX_MA_COLUMNS):
        out[column] = close + (index - 2.5) * 0.01
    return out


@pytest.mark.parametrize("length", [4, 5, 6, 7])
def test_core_box_accepts_only_protocol_lengths(length: int) -> None:
    data = frame()
    start, end = 8, 8 + length - 1
    box = core_box(make_chart_transform(data), data, start_local=start, end_local=end)
    assert box["core_bars"] == length
    assert box["confirmation_bars_inside_box"] == 0
    assert box["contains_core_wicks_and_six_mas"] is True


@pytest.mark.parametrize("length", [3, 8])
def test_core_box_fails_closed_outside_protocol_lengths(length: int) -> None:
    data = frame()
    with pytest.raises(OwnerRecropReviewError, match="4-7"):
        core_box(make_chart_transform(data), data, start_local=5, end_local=5 + length - 1)


def test_stable_context_is_deterministic_and_causal_tip_bounded() -> None:
    values = [stable_context(f"sample-{index}") for index in range(100)]
    assert values == [stable_context(f"sample-{index}") for index in range(100)]
    assert all(10 <= pre <= 12 and 0 <= post <= 2 for pre, post in values)
    assert {post for _, post in values} == {0, 1, 2}


def test_frozen_owner_decision_counts_and_reject_state() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    source_rows = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line]
    overrides = validate_preregistration(prereg, source_rows)
    decisions = [resolve_decision(row, overrides) for row in source_rows]
    assert sum(row["status"] == "OWNER_REJECT" and row["core_bars"] == 0 for row in decisions) == 6
    assert sum(row["status"] == "OWNER_DIRECTED_REBOX_PROPOSAL" for row in decisions) == 5
    assert sum(row["status"] == "OWNER_REFERENCE_RECROP" for row in decisions) == 3
    assert sum(row["status"] == "PENDING_UNMENTIONED" for row in decisions) == 36
    assert sorted(row["core_bars"] for row in decisions if row["core_bars"]) == [4] + [5] * 43


def test_owner_directed_spans_match_feedback_table() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    overrides = {row["sample_id"]: row for row in prereg["owner_decisions"]}
    expected = {
        "346f01e4ae63623fe488123f": (-6, -2),
        "5de0dd06a74bd3d156a51bbc": (-10, -6),
        "a8079908f501a92e573b87cd": (-11, -8),
        "9782cd6e3344064fa0b7d216": (-10, -6),
        "e1d6b8c70518b4785aced64d": (-9, -5),
    }
    assert {
        sample_id: (row["core_start_offset"], row["core_end_offset"])
        for sample_id, row in overrides.items()
        if row["action"] == "rebox"
    } == expected
