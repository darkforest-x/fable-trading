"""Unit tests for P2 hard-negative mining selection."""
from __future__ import annotations

import pytest

from scripts.build_local_signal_v2_p2_hardneg import (
    hard_negative_event_id,
    select_hard_negatives,
)


def _candidate(stem: str, split: str, start: int) -> dict:
    return {
        "stem": stem,
        "symbol": "BTC_USDT_SWAP",
        "split": split,
        "win_start": start,
        "win_len": 30,
    }


def test_selects_candidate_if_any_box_reaches_frozen_threshold():
    rows = [_candidate("a", "train", 100), _candidate("b", "val", 200)]
    predictions = {
        "a": [{"confidence": 0.34}, {"confidence": 0.35}],
        "b": [{"confidence": 0.349999}],
    }
    selected = select_hard_negatives(rows, predictions, 0.35)
    assert [row["stem"] for row in selected] == ["a"]
    assert selected[0]["mining_box_count"] == 1
    assert selected[0]["mining_max_confidence"] == pytest.approx(0.35)
    assert selected[0]["hard_negative_type"] == "b2_false_positive_conf035"


def test_event_id_is_stable_and_split_sensitive():
    train = _candidate("a", "train", 100)
    val = _candidate("a", "val", 100)
    assert hard_negative_event_id(train) == hard_negative_event_id(dict(train))
    assert hard_negative_event_id(train) != hard_negative_event_id(val)


def test_duplicate_candidate_stem_is_rejected():
    rows = [_candidate("a", "train", 100), _candidate("a", "train", 101)]
    with pytest.raises(ValueError, match="duplicate candidate stem"):
        select_hard_negatives(rows, {"a": [{"confidence": 0.9}]}, 0.35)
