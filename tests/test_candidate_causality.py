from __future__ import annotations

import numpy as np
import pandas as pd

from src.judgment import candidates, forward_scan


def _ranked_candidate_frame(periods: int = 400) -> pd.DataFrame:
    scores = np.zeros(periods)
    scores[300] = 1.0
    scores[310] = 10.0
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01", periods=periods, freq="15min", tz="UTC"),
            "shape_score": scores,
        }
    )


def _candidate_mask(frame: pd.DataFrame) -> pd.Series:
    mask = pd.Series(False, index=frame.index)
    for signal_i in (300, 310):
        if signal_i in mask.index:
            mask.loc[signal_i] = True
    return mask


def test_forward_candidates_do_not_change_when_future_higher_score_arrives(monkeypatch) -> None:
    frame = _ranked_candidate_frame()
    monkeypatch.setattr(
        forward_scan,
        "strict_mask",
        lambda enriched, mode="expanded": _candidate_mask(enriched),
    )

    before = forward_scan.forward_candidate_indices(frame.iloc[:302])
    after = forward_scan.forward_candidate_indices(frame.iloc[:320])

    assert before == [300]
    assert after == [300]


def test_training_candidates_keep_the_earliest_gap_signal(monkeypatch) -> None:
    frame = _ranked_candidate_frame()
    monkeypatch.setattr(
        candidates,
        "strict_mask",
        lambda enriched, mode="expanded": _candidate_mask(enriched),
    )

    selected = candidates.scan_candidates(frame, horizon_bars=72, mode="expanded")

    assert selected == [300]
