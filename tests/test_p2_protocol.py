"""P2 pre-registration tests; fixtures only, never the real dataset or holdout."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.judgment.p2_protocol import (
    P2ProtocolError,
    apply_runtime_gate,
    calibrate_runtime_gate,
    prepare_three_way_split,
)


def _row(candidate: str, signal: str, end: str, group: str) -> dict[str, object]:
    start = pd.Timestamp(signal) + pd.Timedelta(minutes=15)
    return {
        "candidate_id": candidate,
        "signal_time": signal,
        "interval_start": start,
        "interval_end": end,
        "event_group_id": group,
    }


def test_three_way_split_purges_boundary_intervals_and_shared_groups() -> None:
    frame = pd.DataFrame(
        [
            _row("train", "2026-03-01T00:00:00Z", "2026-03-01T03:00:00Z", "a"),
            _row("cross", "2026-03-26T23:00:00Z", "2026-03-27T01:00:00Z", "b"),
            _row("early-shared", "2026-03-27T00:15:00Z", "2026-03-27T02:00:00Z", "b"),
            _row("early", "2026-04-01T00:00:00Z", "2026-04-01T03:00:00Z", "c"),
            _row("cal", "2026-04-20T00:00:00Z", "2026-04-20T03:00:00Z", "d"),
        ]
    )
    split = prepare_three_way_split(frame)
    assert split.train["candidate_id"].tolist() == ["train"]
    assert split.early_stop["candidate_id"].tolist() == ["early"]
    assert split.calibration["candidate_id"].tolist() == ["cal"]
    assert set(split.purged["candidate_id"]) == {"cross", "early-shared"}


def test_three_way_split_rejects_holdout_signal_or_label_interval() -> None:
    signal = pd.DataFrame(
        [_row("bad", "2026-05-04T00:00:00Z", "2026-05-04T01:00:00Z", "x")]
    )
    with pytest.raises(P2ProtocolError, match="holdout signal"):
        prepare_three_way_split(signal)
    label = pd.DataFrame(
        [_row("bad", "2026-05-03T23:00:00Z", "2026-05-04T00:00:00Z", "x")]
    )
    with pytest.raises(P2ProtocolError, match="label interval"):
        prepare_three_way_split(label)


def test_runtime_gate_uses_midpoint_when_q90_boundary_is_separable() -> None:
    scores = np.arange(20, dtype=float)
    calibrated = calibrate_runtime_gate(scores)
    assert calibrated["boundary_separable"] is True
    assert calibrated["actual_selected_n"] == 2
    assert calibrated["threshold_equal_n"] == 0
    assert apply_runtime_gate(scores, threshold=calibrated["threshold"]).sum() == 2


def test_runtime_gate_never_slices_a_boundary_tie() -> None:
    scores = np.array([0.0] * 16 + [1.0] * 4)
    calibrated = calibrate_runtime_gate(scores)
    assert calibrated["boundary_separable"] is False
    assert calibrated["target_selected_n"] == 2
    assert calibrated["actual_selected_n"] == 4
    assert calibrated["actual_pass_rate"] == pytest.approx(0.20)
    assert calibrated["health_accepted"] is False
